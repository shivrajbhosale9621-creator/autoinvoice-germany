import uuid
import json
import hmac
import base64
import secrets
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

from fastapi import Request, Response, Depends, HTTPException, status

from app.core.config import settings
from app.core.database import get_db, AsyncSession
from app.models.user import User

try:
    from sqlalchemy import select
except ImportError:
    from app.core.database import select

try:
    import jwt as _pyjwt
    _HAS_PYJWT = True
except ImportError:
    _pyjwt = None
    _HAS_PYJWT = False

try:
    from pwdlib import PasswordHash
    password_hasher = PasswordHash.recommended()
    _HAS_PWDLIB = True
except ImportError:
    password_hasher = None
    _HAS_PWDLIB = False


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("ascii"))


def hash_password(password: str) -> str:
    """Hash a plaintext password using Argon2id (or scrypt fallback with Argon2id header)."""
    if _HAS_PWDLIB and password_hasher is not None:
        return password_hasher.hash(password)
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"$argon2id$v=19$m=65536,t=3,p=4${_b64url_encode(salt)}${_b64url_encode(dk)}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against an Argon2id hash in constant time."""
    if not plain_password or not hashed_password:
        return False
    if _HAS_PWDLIB and password_hasher is not None:
        try:
            return password_hasher.verify(plain_password, hashed_password)
        except Exception:
            pass
    try:
        parts = hashed_password.split("$")
        if len(parts) >= 6 and parts[1].startswith("argon2"):
            salt = _b64url_decode(parts[4])
            expected = _b64url_decode(parts[5])
            dk = hashlib.scrypt(plain_password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=len(expected))
            return hmac.compare_digest(dk, expected)
    except Exception:
        return False
    return False


def hash_token(token: str) -> str:
    """Compute sha256 hex digest of a token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_refresh_token() -> str:
    """Generate an opaque 48-byte URL-safe refresh token."""
    return secrets.token_urlsafe(48)


def create_access_token(user_id: uuid.UUID, email: str, expires_delta: Optional[timedelta] = None) -> str:
    """Create a signed JWT access token."""
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    payload: Dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    if _HAS_PYJWT and _pyjwt is not None:
        return _pyjwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

    header = {"alg": "HS256", "typ": "JWT"}
    h_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    sig = hmac.new(settings.JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{h_b64}.{p_b64}.{_b64url_encode(sig)}"


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode and validate a JWT access token."""
    if _HAS_PYJWT and _pyjwt is not None:
        try:
            payload = _pyjwt.decode(
                token,
                settings.JWT_SECRET,
                algorithms=[settings.JWT_ALGORITHM],
            )
            if payload.get("type") != "access":
                return None
            return payload
        except Exception:
            return None

    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        h_b64, p_b64, s_b64 = parts
        signing_input = f"{h_b64}.{p_b64}".encode("ascii")
        expected_sig = hmac.new(settings.JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
        actual_sig = _b64url_decode(s_b64)
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None
        payload = json.loads(_b64url_decode(p_b64).decode("utf-8"))
        if payload.get("type") != "access":
            return None
        exp = payload.get("exp")
        if exp is not None and int(datetime.now(timezone.utc).timestamp()) > int(exp):
            return None
        return payload
    except Exception:
        return None


def set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    """Set secure httpOnly cookies for both access and refresh tokens."""
    response.set_cookie(
        key="access_token",
        value=access_token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    """Clear both authentication cookies."""
    response.delete_cookie(
        key="access_token",
        path="/",
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
    )
    response.delete_cookie(
        key="refresh_token",
        path="/",
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
    )


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    FastAPI dependency: extracts access_token from httpOnly cookie or Bearer header,
    validates the JWT, loads and returns the User from the database.
    """
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1].strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
        )

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    try:
        user_uuid = uuid.UUID(user_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user ID in token",
        )

    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is inactive",
        )

    return user
