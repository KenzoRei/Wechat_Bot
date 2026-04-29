from sqlalchemy import String, Text, SmallInteger, ForeignKey, UniqueConstraint, DateTime, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
from database import Base
import uuid
from datetime import datetime


class Workflow(Base):
    __tablename__ = "workflow"

    workflow_id: Mapped[uuid.UUID]  = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    name:        Mapped[str]        = mapped_column(String(200), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    created_at:  Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class WorkflowStep(Base):
    __tablename__ = "workflow_step"
    __table_args__ = (
        UniqueConstraint("workflow_id", "step_order", name="uq_workflow_step_order"),
    )

    step_id:     Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    workflow_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow.workflow_id", ondelete="CASCADE"), nullable=False)
    step_order:  Mapped[int]       = mapped_column(SmallInteger, nullable=False)
    step_type:   Mapped[str]       = mapped_column(String(100), nullable=False)
    config:      Mapped[dict]      = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
