from sqlalchemy import String, Text, Numeric, ForeignKey, DateTime, CheckConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB, BYTEA
from database import Base
import uuid
from datetime import datetime


class Customer(Base):
    """
    The company's own customer master record -- one row per real-world
    customer, keyed by the YDD-issued cust_id (format F + 6 digits, e.g.
    F000172). Global, not scoped by group_id -- a deliberate exception to
    how every other table in this codebase is tenant-scoped: this is one
    company's own customer base, looked up consistently across pipelines
    (label creation today; pickup scheduling, claim filing, etc. later),
    not a per-tenant concept.

    Distinct from uchoice_customer (a narrower within-Kefu-conversation
    disambiguation directory) -- kept fully separate on purpose, see
    docs/reviews/active/2026-09-customer-service-and-label-pipeline/plan.md.
    """
    __tablename__ = "customer"
    __table_args__ = (
        CheckConstraint(r"customer_id ~ '^F\d{6}$'", name="ck_customer_id_format"),
        CheckConstraint("status IN ('pending', 'active', 'inactive')", name="ck_customer_status"),
    )

    customer_id:     Mapped[str]        = mapped_column(String(7), primary_key=True)
    display_name:    Mapped[str]        = mapped_column(String(200), nullable=False)
    display_name_cn: Mapped[str | None] = mapped_column(String(200))
    contact_name:    Mapped[str | None] = mapped_column(String(200))
    email:           Mapped[str | None] = mapped_column(String(200))
    phone:           Mapped[str | None] = mapped_column(String(50))
    addr_line1:      Mapped[str | None] = mapped_column(String(300))
    city:            Mapped[str | None] = mapped_column(String(100))
    state:           Mapped[str | None] = mapped_column(String(50))
    zip:             Mapped[str | None] = mapped_column(String(20))
    country:         Mapped[str | None] = mapped_column(String(50))
    bank_account:    Mapped[str | None] = mapped_column(String(200))
    status:          Mapped[str]        = mapped_column(String(20), nullable=False, default="active")
    # {"fedex": 1.3, "ups": 1.25} -- margin over cost, per carrier. NOT read
    # by the label pipeline (YiDiDa already returns its own sales amount,
    # see label_shipment.sales_amount) -- reserved for the future direct-
    # carrier-API pickup pipeline, where no intermediary applies a markup
    # for us.
    rate_multiplier: Mapped[dict]       = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # {"fedex": "channel_a", "ups": "channel_b"} -- NOT a secret, routing config.
    ydd_channel_id:  Mapped[dict]       = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    oms_wh_code:     Mapped[str | None] = mapped_column(String(50))
    # Pricing/business-behavior flags only (e.g. {"ups_incentive": true}).
    # NOT NULL so every reader can .get() without a null-check.
    toggles:         Mapped[dict]       = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    notes:           Mapped[str | None] = mapped_column(Text)
    created_by:      Mapped[str]        = mapped_column(String(128), nullable=False)
    updated_by:      Mapped[str | None] = mapped_column(String(128))
    created_at:      Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at:      Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class CustomerCredential(Base):
    """
    Encrypted per-customer secrets, kept out of the profile table entirely.
    ydd_username/ydd_password are the customer's own YiDiDa login
    credentials -- ydd_username is USUALLY (not always -- documented
    exceptions exist) identical to the customer's own customer_id, but must
    always be stored and read explicitly here, never derived from
    customer.customer_id in code. oms_app_key/oms_app_secret are the
    customer's OMS (xlwms) credentials.

    Write-only at the API layer -- no endpoint ever returns encrypted_value
    or a decrypted value to a browser. Only core.customer_directory's
    get_credentials()/set_credential() touch this table; encrypted_value is
    application-level AES-256-GCM ciphertext, key_version supports future
    master-key rotation without breaking existing rows.
    """
    __tablename__ = "customer_credential"
    __table_args__ = (
        CheckConstraint(
            "credential_type IN ('oms_app_key', 'oms_app_secret', 'ydd_username', 'ydd_password')",
            name="ck_customer_credential_type",
        ),
    )

    customer_id:     Mapped[str]        = mapped_column(String(7), ForeignKey("customer.customer_id", ondelete="CASCADE"), primary_key=True)
    credential_type: Mapped[str]        = mapped_column(String(30), primary_key=True)
    encrypted_value: Mapped[bytes]      = mapped_column(BYTEA, nullable=False)
    key_version:     Mapped[int]        = mapped_column(nullable=False, default=1)
    created_by:      Mapped[str]        = mapped_column(String(128), nullable=False)
    updated_by:      Mapped[str | None] = mapped_column(String(128))
    created_at:      Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at:      Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class LabelShipment(Base):
    """
    Companion ledger to request_log, one row per label -- NOT a replacement
    for it. request_log stays the canonical event record (feeds the
    existing admin Transactions tab, conversation drill-down); this table
    is a thin, query-optimized projection of the label-specific facts
    request_log's generic JSONB result blob can't serve well (which
    customer, which carrier, total charge).

    oms_work_order is set on ANY successful OMS creation (linked or
    standalone/unlinked); NULL only when OMS was skipped (no credentials
    configured) or attempted-and-failed. oms_error distinguishes those two
    NULL cases: set means "attempted, failed"; NULL alongside a NULL
    oms_work_order means "skipped, no credentials". sales_amount is taken
    directly from YiDiDa's own response -- never computed via
    customer.rate_multiplier (that field is reserved for the future direct-
    carrier pickup pipeline).
    """
    __tablename__ = "label_shipment"
    __table_args__ = (
        CheckConstraint("carrier IN ('fedex', 'ups')", name="ck_label_shipment_carrier"),
        CheckConstraint("status IN ('created', 'failed', 'voided')", name="ck_label_shipment_status"),
    )

    shipment_id:         Mapped[uuid.UUID]   = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    request_log_id:      Mapped[uuid.UUID]   = mapped_column(UUID(as_uuid=True), ForeignKey("request_log.log_id", ondelete="CASCADE"), nullable=False, unique=True)
    billing_customer_id: Mapped[str | None]  = mapped_column(String(7), ForeignKey("customer.customer_id"))
    carrier:             Mapped[str]         = mapped_column(String(20), nullable=False)
    tracking_number:     Mapped[str | None]  = mapped_column(String(100))
    oms_work_order:      Mapped[str | None]  = mapped_column(String(100))
    oms_error:           Mapped[str | None]  = mapped_column(Text)
    sales_amount:        Mapped[float | None] = mapped_column(Numeric(12, 2))
    status:              Mapped[str]         = mapped_column(String(20), nullable=False, default="created")
    created_at:          Mapped[datetime]    = mapped_column(DateTime(timezone=True), server_default=text("now()"))
