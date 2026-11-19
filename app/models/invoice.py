import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional
from app.core.database import Base, _HAS_SQLALCHEMY
from app.models.user import utc_now, _parse_dt


if _HAS_SQLALCHEMY:
    from sqlalchemy import String, Float, Integer, Text, DateTime, Uuid, ForeignKey, UniqueConstraint
    from sqlalchemy.orm import Mapped, mapped_column, relationship

    if TYPE_CHECKING:
        from app.models.user import User

    class Invoice(Base):
        __tablename__ = "invoices"
        __table_args__ = (
            UniqueConstraint("user_id", "file_name", name="uq_invoices_user_file"),
        )

        id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
        doc_id: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
        user_id: Mapped[uuid.UUID] = mapped_column(
            Uuid(as_uuid=True),
            ForeignKey("users.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        )
        file_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
        vendor_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
        vendor_address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        vat_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        tax_number: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        vendor_iban: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        vendor_bic: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
        buyer_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
        buyer_address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        buyer_vat_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        leitweg_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
        invoice_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
        document_type: Mapped[Optional[str]] = mapped_column(String(32), default="invoice", nullable=True)
        invoice_date: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
        delivery_date: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
        due_date: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
        payment_terms: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        skonto_terms: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        skonto_percent: Mapped[Optional[float]] = mapped_column(Float, default=0.0, nullable=True)
        skonto_days: Mapped[Optional[int]] = mapped_column(Integer, default=0, nullable=True)
        skonto_deadline: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
        skonto_amount: Mapped[Optional[float]] = mapped_column(Float, default=0.0, nullable=True)
        items: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        vat_breakdown: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        subtotal: Mapped[Optional[float]] = mapped_column(Float, default=0.0, nullable=True)
        tax: Mapped[Optional[float]] = mapped_column(Float, default=0.0, nullable=True)
        tax_amount: Mapped[Optional[float]] = mapped_column(Float, default=0.0, nullable=True)
        discount: Mapped[Optional[float]] = mapped_column(Float, default=0.0, nullable=True)
        total_amount: Mapped[Optional[float]] = mapped_column(Float, default=0.0, nullable=True)
        currency: Mapped[Optional[str]] = mapped_column(String(16), default="EUR", nullable=True)
        payment_status: Mapped[Optional[str]] = mapped_column(String(32), default="Unpaid", nullable=True)
        validation_report: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        validation_status: Mapped[Optional[str]] = mapped_column(String(32), default="PENDING", nullable=True)
        vies_status: Mapped[Optional[str]] = mapped_column(String(32), default="unchecked", nullable=True)
        vies_checked_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        vies_details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        iban_change_flag: Mapped[Optional[int]] = mapped_column(Integer, default=0, nullable=True)
        previous_iban: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        duplicate_flag: Mapped[Optional[int]] = mapped_column(Integer, default=0, nullable=True)
        duplicate_of: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
        datev_booked: Mapped[Optional[int]] = mapped_column(Integer, default=0, nullable=True)
        datev_account: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
        payment_run_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        source_currency: Mapped[Optional[str]] = mapped_column(String(16), default="EUR", nullable=True)
        fx_rate: Mapped[Optional[float]] = mapped_column(Float, default=1.0, nullable=True)
        fx_rate_date: Mapped[Optional[str]] = mapped_column(String(32), default="", nullable=True)
        fx_rate_source: Mapped[Optional[str]] = mapped_column(String(32), default="", nullable=True)
        tax_treatment: Mapped[Optional[str]] = mapped_column(String(32), default="standard", nullable=True)
        original_net: Mapped[Optional[float]] = mapped_column(Float, default=0.0, nullable=True)
        original_vat: Mapped[Optional[float]] = mapped_column(Float, default=0.0, nullable=True)
        original_gross: Mapped[Optional[float]] = mapped_column(Float, default=0.0, nullable=True)
        supplier_country: Mapped[Optional[str]] = mapped_column(String(8), default="", nullable=True)
        uploaded_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        created_at: Mapped[Optional[datetime]] = mapped_column(
            DateTime(timezone=True),
            default=utc_now,
            nullable=True,
        )

        user: Mapped["User"] = relationship("User", back_populates="invoices")

        def __repr__(self) -> str:
            return f"<Invoice id={self.id} user_id={self.user_id} invoice_number={self.invoice_number} total={self.total_amount}>"

else:
    from app.core.database import _ColumnDescriptor

    class Invoice(Base):
        __tablename__ = "invoices"

        def __init__(self, **kwargs: Any):
            self.id = kwargs.get("id")
            self.doc_id = kwargs.get("doc_id")
            uid = kwargs.get("user_id")
            try:
                self.user_id = uuid.UUID(str(uid)) if uid else None
            except Exception:
                self.user_id = uid
            self.file_name = kwargs.get("file_name")
            self.vendor_name = kwargs.get("vendor_name")
            self.vendor_address = kwargs.get("vendor_address")
            self.vat_id = kwargs.get("vat_id")
            self.tax_number = kwargs.get("tax_number")
            self.vendor_iban = kwargs.get("vendor_iban")
            self.vendor_bic = kwargs.get("vendor_bic")
            self.buyer_name = kwargs.get("buyer_name")
            self.buyer_address = kwargs.get("buyer_address")
            self.buyer_vat_id = kwargs.get("buyer_vat_id")
            self.leitweg_id = kwargs.get("leitweg_id")
            self.invoice_number = kwargs.get("invoice_number")
            self.document_type = kwargs.get("document_type", "invoice")
            self.invoice_date = kwargs.get("invoice_date")
            self.delivery_date = kwargs.get("delivery_date")
            self.due_date = kwargs.get("due_date")
            self.payment_terms = kwargs.get("payment_terms")
            self.skonto_terms = kwargs.get("skonto_terms")
            self.skonto_percent = float(kwargs.get("skonto_percent") or 0.0)
            self.skonto_days = int(kwargs.get("skonto_days") or 0)
            self.skonto_deadline = kwargs.get("skonto_deadline")
            self.skonto_amount = float(kwargs.get("skonto_amount") or 0.0)
            self.items = kwargs.get("items")
            self.vat_breakdown = kwargs.get("vat_breakdown")
            self.subtotal = float(kwargs.get("subtotal") or 0.0)
            self.tax = float(kwargs.get("tax") or kwargs.get("tax_amount") or 0.0)
            self.tax_amount = float(kwargs.get("tax_amount") or kwargs.get("tax") or 0.0)
            self.discount = float(kwargs.get("discount") or 0.0)
            self.total_amount = float(kwargs.get("total_amount") or 0.0)
            self.currency = kwargs.get("currency", "EUR")
            self.payment_status = kwargs.get("payment_status", "Unpaid")
            self.validation_report = kwargs.get("validation_report")
            self.validation_status = kwargs.get("validation_status", "PENDING")
            self.vies_status = kwargs.get("vies_status", "unchecked")
            self.vies_checked_at = kwargs.get("vies_checked_at")
            self.vies_details = kwargs.get("vies_details")
            self.iban_change_flag = int(kwargs.get("iban_change_flag") or 0)
            self.previous_iban = kwargs.get("previous_iban", "")
            self.duplicate_flag = int(kwargs.get("duplicate_flag") or 0)
            self.duplicate_of = kwargs.get("duplicate_of", "")
            self.datev_booked = int(kwargs.get("datev_booked") or 0)
            self.datev_account = kwargs.get("datev_account", "")
            self.payment_run_id = kwargs.get("payment_run_id", "")
            self.uploaded_at = kwargs.get("uploaded_at")
            self.created_at = _parse_dt(kwargs.get("created_at"))

        @classmethod
        def _from_row(cls, row: dict) -> "Invoice":
            return cls(**row)

    Invoice.id = _ColumnDescriptor(Invoice, "id")
    Invoice.doc_id = _ColumnDescriptor(Invoice, "doc_id")
    Invoice.user_id = _ColumnDescriptor(Invoice, "user_id")
    Invoice.file_name = _ColumnDescriptor(Invoice, "file_name")
    Invoice.invoice_number = _ColumnDescriptor(Invoice, "invoice_number")
