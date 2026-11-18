import uuid
from datetime import datetime, timezone
from typing import List, TYPE_CHECKING, Any
from app.core.database import Base, _HAS_SQLALCHEMY


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(val: Any) -> datetime:
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, str) and val:
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return utc_now()


if _HAS_SQLALCHEMY:
    from sqlalchemy import String, Boolean, DateTime, Uuid
    from sqlalchemy.orm import Mapped, mapped_column, relationship

    if TYPE_CHECKING:
        from app.models.otp import OtpCode
        from app.models.token import RefreshToken
        from app.models.invoice import Invoice

    class User(Base):
        __tablename__ = "users"

        id: Mapped[uuid.UUID] = mapped_column(
            Uuid(as_uuid=True),
            primary_key=True,
            default=uuid.uuid4,
        )
        email: Mapped[str] = mapped_column(
            String(255),
            unique=True,
            index=True,
            nullable=False,
        )
        password_hash: Mapped[str] = mapped_column(
            String(255),
            nullable=False,
        )
        is_verified: Mapped[bool] = mapped_column(
            Boolean,
            default=False,
            nullable=False,
        )
        is_active: Mapped[bool] = mapped_column(
            Boolean,
            default=True,
            nullable=False,
        )
        created_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True),
            default=utc_now,
            nullable=False,
        )

        otp_codes: Mapped[List["OtpCode"]] = relationship(
            "OtpCode",
            back_populates="user",
            cascade="all, delete-orphan",
        )
        refresh_tokens: Mapped[List["RefreshToken"]] = relationship(
            "RefreshToken",
            back_populates="user",
            cascade="all, delete-orphan",
        )
        invoices: Mapped[List["Invoice"]] = relationship(
            "Invoice",
            back_populates="user",
            cascade="all, delete-orphan",
        )

        def __repr__(self) -> str:
            return f"<User id={self.id} email={self.email} is_verified={self.is_verified}>"

else:
    from app.core.database import _ColumnDescriptor

    class User(Base):
        __tablename__ = "users"

        def __init__(
            self,
            id: Any = None,
            email: str = "",
            password_hash: str = "",
            is_verified: bool = False,
            is_active: bool = True,
            created_at: Any = None,
        ):
            self.id = uuid.UUID(str(id)) if id else uuid.uuid4()
            self.email = email
            self.password_hash = password_hash
            self.is_verified = bool(is_verified)
            self.is_active = bool(is_active)
            self.created_at = _parse_dt(created_at)

        @classmethod
        def _from_row(cls, row: dict) -> "User":
            return cls(
                id=row.get("id"),
                email=row.get("email", ""),
                password_hash=row.get("password_hash", ""),
                is_verified=bool(row.get("is_verified", 0)),
                is_active=bool(row.get("is_active", 1)),
                created_at=row.get("created_at"),
            )

        def _save_to_conn(self, conn: Any) -> None:
            conn.execute(
                """
                INSERT INTO users (id, email, password_hash, is_verified, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    email=excluded.email,
                    password_hash=excluded.password_hash,
                    is_verified=excluded.is_verified,
                    is_active=excluded.is_active
                """,
                (
                    self.id.hex,
                    self.email,
                    self.password_hash,
                    1 if self.is_verified else 0,
                    1 if self.is_active else 0,
                    self.created_at.isoformat(),
                ),
            )

        def __repr__(self) -> str:
            return f"<User id={self.id} email={self.email} is_verified={self.is_verified}>"

    User.id = _ColumnDescriptor(User, "id")
    User.email = _ColumnDescriptor(User, "email")
    User.is_verified = _ColumnDescriptor(User, "is_verified")
    User.is_active = _ColumnDescriptor(User, "is_active")
