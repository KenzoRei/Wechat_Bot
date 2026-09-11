from sqlalchemy import String, DateTime, text
from sqlalchemy.orm import Mapped, mapped_column
from database import Base
from datetime import datetime


class CompanyWarehouse(Base):
    """
    The company's own physical warehouse/shipping-origin directory --
    address, contact, phone, email per location. Deliberately separate
    from core.uchoice_constants.VALID_WAREHOUSE_CODES (JFK/DE/NJ only,
    U-Choice's own inventory-tracking warehouse_code concept): this table
    is company-wide shipping-origin info used by the label pipeline
    (fedex_label/ups_label shipper_* fields), covering every physical
    location (including LAX/ORD, which never held U-Choice inventory).
    Global, not scoped by group_id, same reasoning as Customer.
    """
    __tablename__ = "company_warehouse"

    warehouse_abbr: Mapped[str]        = mapped_column(String(10), primary_key=True)
    # The legal/billing entity operating this location -- NOT always the
    # same across warehouses (e.g. JFK/DE/LAX/ORD are all "TWF-*", NJ is
    # the separate "TWW" entity). Maps to shipper_corp_name on a label.
    company_name:   Mapped[str]        = mapped_column(String(200), nullable=False)
    addr:           Mapped[str]        = mapped_column(String(300), nullable=False)
    city:           Mapped[str]        = mapped_column(String(100), nullable=False)
    state:          Mapped[str]        = mapped_column(String(50), nullable=False)
    zip_code:       Mapped[str]        = mapped_column(String(20), nullable=False)
    contact:        Mapped[str | None] = mapped_column(String(200))
    phone:          Mapped[str | None] = mapped_column(String(50))
    email:          Mapped[str | None] = mapped_column(String(200))
    created_by:     Mapped[str]        = mapped_column(String(128), nullable=False)
    updated_by:     Mapped[str | None] = mapped_column(String(128))
    created_at:     Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at:     Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=text("now()"))
