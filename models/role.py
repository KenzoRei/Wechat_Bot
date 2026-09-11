from sqlalchemy import String, DateTime, ForeignKey, text
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


class RoleServicePermission(Base):
    """
    Deny-by-default permission grant: a role has zero access to a service
    unless a matching row exists here -- GLOBAL, not per-group. Previously
    this was group_service_role (group_id, service_type_id, role_id),
    re-declaring the same role->service mapping separately per WeCom
    group/tenant. In practice every real grant ever made targeted the
    same single group, and a role represents a job function (warehouseman,
    label_agent, ...) that should behave identically regardless of which
    tenant a person belongs to -- the group axis added no real
    differentiation, just per-group admin busywork. Real multi-tenancy
    (which services a given tenant even has access to at all) still lives
    in group_service, untouched by this change; a role's actual reachable
    services in a given group are role_service_permission ∩ group_service
    (see core/access_control.py).
    """
    __tablename__ = "role_service_permission"

    role_id:         Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("role.role_id", ondelete="CASCADE"), primary_key=True)
    service_type_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("service_type.service_type_id", ondelete="CASCADE"), primary_key=True)
    created_by:      Mapped[str]       = mapped_column(String(128), nullable=False)
    created_at:      Mapped[datetime]  = mapped_column(DateTime(timezone=True), server_default=text("now()"))
