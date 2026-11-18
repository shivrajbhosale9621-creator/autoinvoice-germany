import uuid
import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from app.core.database import Base, _HAS_SQLALCHEMY
from app.models.user import utc_now, _parse_dt


class OtpPurpose(str, enum.Enum):
    EMAIL_VERIFICATION = "email_verification"
    PASSWORD_RESET = "password_reset"


if _HAS_SQLALCHEMY:
    from sqlalchemy import String, Integer, Boolean, DateTime, Uuid, ForeignKey, Enum
    from sqlalchemy.orm import Mapped, mapped_column, relationship

    if TYPE_CHECKING:
        from app.models.user import User

    class OtpCode(Base):
        __tablename__ = "otp_codes"

        id: Mapped[uuid.UUID] = mapped_column(
            Uuid(as_uuid=True),
            primary_key=True,
            default=uuid.uuid4,
        )
        user_id: Mapped[uuid.UUID] = mapped_column(
            Uuid(as_uuid=True),
            ForeignKey("users.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        )
        purpose: Mapped[OtpPurpose] = mapped_column(
            Enum(OtpPurpose, name="otp_purpose", native_enum=False),
            nullable=False,
            index=True,
        )
        code_hash: Mapped[str] = mapped_column(
            String(64),
            nullable=False,
        )
        expires_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True),
            nullable=False,
        )
        attempts: Mapped[int] = mapped_column(
            Integer,
            default=0,
            nullable=False,
        )
        consumed: Mapped[bool] = mapped_column(
            Boolean,
            default=False,
            nullable=False,
        )
        created_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True),
            default=utc_now,
            nullable=False,
        )

        user: Mapped["User"] = relationship("User", back_populates="otp_codes")

        def __repr__(self) -> str:
            return f"<OtpCode id={self.id} user_id={self.user_id} purpose={self.purpose} consumed={self.consumed}>"

else:
    from app.core.database import _ColumnDescriptor

    class OtpCode(Base):
        __tablename__ = "otp_codes"

        def __init__(
            self,
            id: Any = None,
            user_id: Any = None,
            purpose: Any = OtpPurpose.EMAIL_VERIFICATION,
            code_hash: str = "",
            expires_at: Any = None,
            attempts: int = 0,
            consumed: bool = False,
            created_at: Any = None,
        ):
            self.id = uuid.UUID(str(id)) if id else uuid.uuid4()
            self.user_id = uuid.UUID(str(user_id)) if user_id else uuid.uuid4()
            self.purpose = OtpPurpose(purpose.value if hasattr(purpose, "value") else str(purpose))
            self.code_hash = code_hash
            self.expires_at = _parse_dt(expires_at)
            self.attempts = int(attempts or 0)
            self.consumed = bool(consumed)
            self.created_at = _parse_dt(created_at)

        @classmethod
        def _from_row(cls, row: dict) -> "OtpCode":
            return cls(
                id=row.get("id"),
                user_id=row.get("user_id"),
                purpose=row.get("purpose", "email_verification"),
                code_hash=row.get("code_hash", ""),
                expires_at=row.get("expires_at"),
                attempts=int(row.get("attempts", 0)),
                consumed=bool(row.get("consumed", 0)),
                created_at=row.get("created_at"),
            )

        def _save_to_conn(self, conn: Any) -> None:
            conn.execute(
                """
                INSERT INTO otp_codes (id, user_id, purpose, code_hash, expires_at, attempts, consumed, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    attempts=excluded.attempts,
                    consumed=excluded.consumed,
                    code_hash=excluded.code_hash,
                    expires_at=excluded.expires_at
                """,
                (
                    self.id.hex,
                    self.user_id.hex,
                    self.purpose.value if hasattr(self.purpose, "value") else str(self.purpose),
                    self.code_hash,
                    self.expires_at.isoformat(),
                    int(self.attempts),
                    1 if self.consumed else 0,
                    self.created_at.isoformat(),
                ),
            )

    OtpCode.id = _ColumnDescriptor(OtpCode, "id")
    OtpCode.user_id = _ColumnDescriptor(OtpCode, "user_id")
    OtpCode.purpose = _ColumnDescriptor(OtpCode, "purpose")
    OtpCode.consumed = _ColumnDescriptor(OtpCode, "consumed")
    OtpCode.expires_at = _ColumnDescriptor(OtpCode, "expires_at")
    OtpCode.created_at = _ColumnDescriptor(OtpCode, "created_at")
