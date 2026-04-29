from sqlalchemy import String, Boolean, Text, DateTime, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
from database import Base
import uuid
from datetime import datetime


class ServiceType(Base):
    __tablename__ = "service_type"

    service_type_id:     Mapped[uuid.UUID]  = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    name:                Mapped[str]        = mapped_column(String(100), nullable=False, unique=True)
    description:         Mapped[str | None] = mapped_column(String(500))
    input_schema:        Mapped[dict]       = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    group_config_schema: Mapped[dict]       = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    confirmation_note:   Mapped[str | None] = mapped_column(Text)
    is_active:           Mapped[bool]       = mapped_column(Boolean, nullable=False, default=True)
    created_at:          Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=text("now()"))
