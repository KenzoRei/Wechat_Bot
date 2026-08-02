from sqlalchemy import String, DateTime, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
from database import Base
import uuid
from datetime import datetime


class Role(Base):
    """
    Role catalog — replaces the old hardcoded VALID_ROLES set in api/admin/members.py.
    New roles (e.g. "warehouseman", "accountant" for U-Choice) are added via the
    admin API, no redeploy needed.
    """
    __tablename__ = "role"

    role_id:     Mapped[uuid.UUID]  = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    name:        Mapped[str]        = mapped_column(String(20), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(200))
    created_at:  Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=text("now()"))
