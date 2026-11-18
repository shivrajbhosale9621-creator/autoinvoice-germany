import re
import uuid
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Request, Response, Depends, HTTPException, status
from pydantic import BaseModel, Field

try:
    import email_validator  # noqa: F401
    from pydantic import EmailStr
except ImportError:
    class EmailStr(str):
        @classmethod
        def __get_validators__(cls):
            yield cls.validate

        @classmethod
        def validate(cls, v):
            if not isinstance(v, str) or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v.strip()):
                raise ValueError("Invalid email address")
            return v.strip()

try:
    from sqlalchemy import select, update
except ImportError:
    from app.core.database import select, update

from app.core.config import settings
from app.core.database import get_db, AsyncSession
from app.core.rate_limiter import limiter
from app.core.security import (
    hash_password,
    verify_password,
    hash_token,
    generate_refresh_token,
    create_access_token,
    set_auth_cookies,
    clear_auth_cookies,
    get_current_user,
)
from app.models.user import User
from app.models.otp import OtpCode, OtpPurpose
from app.models.token import RefreshToken
from app.services.mailer import send_otp_email, BrevoEmailError
from app.services.otp import (
    create_and_store_otp,
    verify_and_consume_otp,
    OtpCooldownError,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, description="Password must be at least 8 characters")


class VerifyOtpRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6, description="6-digit verification code")
    purpose: OtpPurpose = OtpPurpose.EMAIL_VERIFICATION


class ResendOtpRequest(BaseModel):
    email: EmailStr
    purpose: OtpPurpose = OtpPurpose.EMAIL_VERIFICATION


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=8)


async def issue_tokens_for_user(
    db: AsyncSession,
    user: User,
    response: Response,
) -> tuple[str, str]:
    """Issue access token + refresh token, persist refresh hash, and set cookies."""
    now = datetime.now(timezone.utc)
    access_token = create_access_token(user_id=user.id, email=user.email)
    raw_refresh_token = generate_refresh_token()
    token_digest = hash_token(raw_refresh_token)
    expires_at = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    db_token = RefreshToken(
        user_id=user.id,
        token_hash=token_digest,
        expires_at=expires_at,
        revoked=False,
        created_at=now,
    )
    db.add(db_token)
    await db.commit()

    set_auth_cookies(response, access_token=access_token, refresh_token=raw_refresh_token)
    return access_token, raw_refresh_token


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(payload: SignupRequest, db: AsyncSession = Depends(get_db)):
    clean_email = payload.email.lower().strip()

    existing = await db.execute(select(User).where(User.email == clean_email))
    existing_user = existing.scalar_one_or_none()
    if existing_user and existing_user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"status": "error", "error": "Email already registered"},
        )

    if existing_user:
        existing_user.password_hash = hash_password(payload.password)
        await db.commit()
        await db.refresh(existing_user)
        user = existing_user
    else:
        pwd_hash = hash_password(payload.password)
        user = User(
            email=clean_email,
            password_hash=pwd_hash,
            is_verified=False,
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    try:
        otp_code = await create_and_store_otp(
            db=db,
            user_id=user.id,
            purpose=OtpPurpose.EMAIL_VERIFICATION,
            enforce_cooldown=False,
        )
        await send_otp_email(clean_email, otp_code, purpose="email_verification")
    except BrevoEmailError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"status": "error", "error": "could not send email, use resend"},
        ) from exc

    return {
        "status": "success",
        "message": "verification code sent",
        "email": clean_email,
    }


@router.post("/verify-otp")
@limiter.limit("10/minute")
async def verify_otp(
    request: Request,
    response: Response,
    payload: VerifyOtpRequest,
    db: AsyncSession = Depends(get_db),
):
    clean_email = payload.email.lower().strip()
    result = await db.execute(select(User).where(User.email == clean_email))
    user = result.scalar_one_or_none()

    generic_error = {"status": "error", "error": "invalid or expired code"}

    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=generic_error)

    verified = await verify_and_consume_otp(
        db=db,
        user_id=user.id,
        code=payload.code,
        purpose=payload.purpose,
    )

    if not verified:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=generic_error)

    if payload.purpose == OtpPurpose.EMAIL_VERIFICATION:
        user.is_verified = True
        if not hasattr(db, "_pending_adds"):
            await db.commit()
        else:
            db.add(user)
            await db.commit()
        await db.refresh(user)

    await issue_tokens_for_user(db, user, response)

    return {"status": "success", "message": "verified"}


@router.post("/resend-otp")
@limiter.limit("3/minute")
async def resend_otp(
    request: Request,
    payload: ResendOtpRequest,
    db: AsyncSession = Depends(get_db),
):
    clean_email = payload.email.lower().strip()
    result = await db.execute(select(User).where(User.email == clean_email))
    user = result.scalar_one_or_none()

    generic_response = {
        "status": "success",
        "message": "If the account exists, a code was sent",
    }

    if not user:
        return generic_response

    try:
        otp_code = await create_and_store_otp(
            db=db,
            user_id=user.id,
            purpose=payload.purpose,
            enforce_cooldown=True,
        )
        await send_otp_email(clean_email, otp_code, purpose=payload.purpose.value)
    except OtpCooldownError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "status": "error",
                "error": "Please wait before requesting another code",
                "retry_after": exc.retry_after,
            },
        )
    except BrevoEmailError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"status": "error", "error": "could not send email, use resend"},
        ) from exc

    return generic_response


@router.post("/login")
@limiter.limit("5/minute")
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    clean_email = payload.email.lower().strip()
    result = await db.execute(select(User).where(User.email == clean_email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(payload.password, user.password_hash):
        await asyncio.sleep(0.05)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "error": "Invalid email or password"},
        )

    if not user.is_verified:
        try:
            otp_code = await create_and_store_otp(
                db=db,
                user_id=user.id,
                purpose=OtpPurpose.EMAIL_VERIFICATION,
                enforce_cooldown=False,
            )
            await send_otp_email(clean_email, otp_code, purpose="email_verification")
        except Exception:
            pass

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "status": "error",
                "error": "unverified",
                "message": "verify your email first",
                "email": clean_email,
            },
        )

    await issue_tokens_for_user(db, user, response)

    return {
        "status": "success",
        "message": "Logged in successfully",
        "user": {
            "id": str(user.id),
            "email": user.email,
        },
    }


@router.post("/forgot-password")
async def forgot_password(
    payload: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    clean_email = payload.email.lower().strip()
    result = await db.execute(select(User).where(User.email == clean_email))
    user = result.scalar_one_or_none()

    generic_response = {
        "status": "success",
        "message": "if the account exists, a code was sent",
    }

    if user and user.is_verified:
        try:
            otp_code = await create_and_store_otp(
                db=db,
                user_id=user.id,
                purpose=OtpPurpose.PASSWORD_RESET,
                enforce_cooldown=False,
            )
            await send_otp_email(clean_email, otp_code, purpose="password_reset")
        except BrevoEmailError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"status": "error", "error": "could not send email, use resend"},
            ) from exc

    return generic_response


@router.post("/reset-password")
async def reset_password(
    payload: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    clean_email = payload.email.lower().strip()
    result = await db.execute(select(User).where(User.email == clean_email))
    user = result.scalar_one_or_none()

    generic_error = {"status": "error", "error": "invalid or expired code"}

    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=generic_error)

    verified = await verify_and_consume_otp(
        db=db,
        user_id=user.id,
        code=payload.code,
        purpose=OtpPurpose.PASSWORD_RESET,
    )

    if not verified:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=generic_error)

    user.password_hash = hash_password(payload.new_password)
    if hasattr(db, "_pending_adds"):
        db.add(user)

    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id)
        .values(revoked=True)
    )
    await db.commit()

    return {
        "status": "success",
        "message": "Password reset successfully. Please log in with your new password.",
    }


@router.post("/refresh")
async def refresh_tokens(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    raw_token = request.cookies.get("refresh_token")
    if not raw_token:
        clear_auth_cookies(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "error": "Refresh token missing"},
        )

    now = datetime.now(timezone.utc)
    token_digest = hash_token(raw_token)

    stmt = select(RefreshToken).where(
        RefreshToken.token_hash == token_digest,
        RefreshToken.revoked == False,
        RefreshToken.expires_at > now,
    )
    result = await db.execute(stmt)
    db_token = result.scalar_one_or_none()

    if not db_token:
        clear_auth_cookies(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "error": "Invalid or expired refresh token"},
        )

    db_token.revoked = True
    if hasattr(db, "_pending_adds"):
        db.add(db_token)

    user_result = await db.execute(select(User).where(User.id == db_token.user_id))
    user = user_result.scalar_one_or_none()

    if not user or not user.is_active:
        clear_auth_cookies(response)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "error": "User account is unavailable"},
        )

    await issue_tokens_for_user(db, user, response)

    return {"status": "success", "message": "Tokens refreshed"}


@router.get("/me")
async def get_me(user: User = Depends(get_current_user)):
    return {
        "status": "success",
        "user": {
            "id": str(user.id),
            "email": user.email,
            "is_verified": user.is_verified,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        },
    }


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    raw_token = request.cookies.get("refresh_token")
    if raw_token:
        token_digest = hash_token(raw_token)
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == token_digest)
            .values(revoked=True)
        )
        await db.commit()

    clear_auth_cookies(response)
    return {"status": "success", "message": "Logged out successfully"}
