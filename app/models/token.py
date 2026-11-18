import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from app.core.database import Base, _HAS_SQLALCHEMY
from app.models.user import utc_now, _parse_dt


if _HAS_SQLALCHEMY:
    from sqlalchemy import String, Boolean, DateTime, Uuid, ForeignKey
    from sqlalchemy.orm import Mapped, mapped_column, relationship

    if TYPE_CHECKING:
        from app.models.user import User

    class RefreshToken(Base):
        __tablename__ = "refresh_tokens"

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
        token_hash: Mapped[str] = mapped_column(
            String(64),
            index=True,
            nullable=False,
        )
        expires_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True),
            nullable=False,
        )
        revoked: Mapped[bool] = mapped_column(
            Boolean,
            default=False,
            nullable=False,
        )
        created_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True),
            default=utc_now,
            nullable=False,
        )

        user: Mapped["User"] = relationship("User", back_populates="refresh_tokens")

        def __repr__(self) -> str:
            return f"<RefreshToken id={self.id} user_id={self.user_id} revoked={self.revoked}>"

else:
    from app.core.database import _ColumnDescriptor

    class RefreshToken(Base):
        __tablename__ = "refresh_tokens"

        def __init__(
            self,
            id: Any = None,
            user_id: Any = None,
            token_hash: str = "",
            expires_at: Any = None,
            revoked: bool = False,
            created_at: Any = None,
        ):
            self.id = uuid.UUID(str(id)) if id else uuid.uuid4()
            self.user_id = uuid.UUID(str(user_id)) if user_id else uuid.uuid4()
            self.token_hash = token_hash
            self.expires_at = _parse_dt(expires_at)
            self.revoked = bool(revoked)
            self.created_at = _parse_dt(created_at)

        @classmethod
        def _from_row(cls, row: dict) -> "RefreshToken":
            return cls(
                id=row.get("id"),
                user_id=row.get("user_id"),
                token_hash=row.get("token_hash", ""),
                expires_at=row.get("expires_at"),
                revoked=bool(row.get("revoked", 0)),
                created_at=row.get("created_at"),
            )

        def _save_to_conn(self, conn: Any) -> None:
            conn.execute(
                """
                INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at, revoked, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    revoked=excluded.revoked
                """,
                (
                    self.id.hex,
                    self.user_id.hex,
                    self.token_hash,
                    self.expires_at.isoformat(),
                    1 if self.revoked else 0,
                    self.created_at.isoformat(),
                ),
            )

    RefreshToken.id = _ColumnDescriptor(RefreshToken, "id")
    RefreshToken.user_id = _ColumnDescriptor(RefreshToken, "user_id")
    RefreshToken.token_hash = _ColumnDescriptor(RefreshToken, "token_hash")
    RefreshToken.revoked = _ColumnDescriptor(RefreshToken, "revoked")
    RefreshToken.expires_at = _ColumnDescriptor(RefreshToken, "expires_at")
