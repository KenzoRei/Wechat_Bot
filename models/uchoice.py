from sqlalchemy import String, Text, Integer, Numeric, Date, ForeignKey, DateTime, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
from database import Base
import uuid
from datetime import datetime, date
from decimal import Decimal


class UchoiceSku(Base):
    __tablename__ = "uchoice_sku"

    sku_code:    Mapped[str] = mapped_column(String(50), primary_key=True)
    description: Mapped[str] = mapped_column(String(200), nullable=False)


class UchoiceStorage(Base):
    """
    Current balance per (warehouse, sku, boxes_per_pallet) bucket. No group_id —
    U-Choice owns this inventory itself, it isn't segregated per customer.
    boxes_per_pallet is a free integer, not a pre-registered catalog value —
    buckets are created dynamically as real box counts drift from partial picks.
    """
    __tablename__ = "uchoice_storage"

    warehouse_code:   Mapped[str]      = mapped_column(String(20), primary_key=True)
    sku_code:         Mapped[str]      = mapped_column(String(50), ForeignKey("uchoice_sku.sku_code"), primary_key=True)
    boxes_per_pallet: Mapped[int]      = mapped_column(Integer, primary_key=True)
    pallet_count:     Mapped[int]      = mapped_column(Integer, nullable=False, default=0)
    updated_at:       Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class UchoiceStorageTxn(Base):
    __tablename__ = "uchoice_storage_txn"

    txn_id:           Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    warehouse_code:   Mapped[str]              = mapped_column(String(20), nullable=False)
    sku_code:         Mapped[str]              = mapped_column(String(50), nullable=False)
    boxes_per_pallet: Mapped[int]              = mapped_column(Integer, nullable=False)
    pallet_delta:     Mapped[int]              = mapped_column(Integer, nullable=False)
    txn_type:         Mapped[str]              = mapped_column(String(20), nullable=False)
    request_log_id:   Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("request_log.log_id", ondelete="SET NULL"))
    note:              Mapped[str | None]      = mapped_column(Text)
    created_by:        Mapped[str]             = mapped_column(String(128), nullable=False)
    created_at:         Mapped[datetime]       = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class UchoiceAddress(Base):
    __tablename__ = "uchoice_address"

    address_id:     Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    company_name:   Mapped[str]            = mapped_column(String(200), nullable=False)
    charge_type:    Mapped[str]            = mapped_column(String(20), nullable=False)
    addr:           Mapped[str]            = mapped_column(Text, nullable=False)
    warehouse_code: Mapped[str | None]     = mapped_column(String(20))
    note:           Mapped[str | None]     = mapped_column(Text)
    created_by:     Mapped[str]            = mapped_column(String(128), nullable=False)
    created_at:     Mapped[datetime]       = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    # Set only on the two seeded inter-warehouse addresses — marks this
    # address as "one of our own warehouses" and names which one, so an
    # outbound to it triggers a storage increase there too, not just a
    # decrease at the origin.
    destination_warehouse_code: Mapped[str | None] = mapped_column(String(20))


class UchoiceStorageFeeLedger(Base):
    __tablename__ = "uchoice_storage_fee_ledger"
    __table_args__ = (
        UniqueConstraint("warehouse_code", "fee_date", name="uq_storage_fee_ledger_wh_date"),
    )

    ledger_id:      Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    warehouse_code: Mapped[str]       = mapped_column(String(20), nullable=False)
    fee_date:       Mapped[date]      = mapped_column(Date, nullable=False)
    pallet_count:   Mapped[int]       = mapped_column(Integer, nullable=False)
    storage_fee:    Mapped[Decimal]   = mapped_column(Numeric(10, 2), nullable=False)
