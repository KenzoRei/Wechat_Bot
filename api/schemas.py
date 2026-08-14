"""
Pydantic request and response schemas for all API endpoints.
FastAPI uses these for automatic request validation and response serialization.
"""
from pydantic import BaseModel, Field, ConfigDict
from uuid import UUID
from datetime import datetime
from typing import Any


# ── Groups ────────────────────────────────────────────────────────────────────

class GroupCreate(BaseModel):
    wechat_group_id:     str
    description:         str | None = None
    daily_request_limit: int | None = None
    context:             dict | None = None   # location presets, aliases, etc.


class GroupUpdate(BaseModel):
    description:             str | None = None
    is_active:               bool | None = None
    daily_request_limit:     int | None = None
    context:                 dict | None = None   # pass null to clear, omit to leave unchanged
    group_robot_webhook_url: str | None = None     # pass null to clear, omit to leave unchanged


class GroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    group_id:                UUID
    wechat_group_id:         str
    description:              str | None
    is_active:                bool
    daily_request_limit:      int | None
    context:                  dict | None
    group_robot_webhook_url:  str | None
    created_at:                datetime


# ── Members ───────────────────────────────────────────────────────────────────

class MemberCreate(BaseModel):
    wechat_openid:  str
    role:           str
    display_name:   str | None = None
    warehouse_code: str | None = None   # required if role == "warehouseman", enforced in the route


class MemberUpdate(BaseModel):
    role:           str | None = None
    is_active:      bool | None = None
    warehouse_code: str | None = None   # required if role becomes "warehouseman", enforced in the route


class MemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    wechat_openid:  str
    group_id:       UUID
    role:           str
    display_name:   str | None
    warehouse_code: str | None
    is_active:      bool
    joined_at:      datetime


# ── Roles ─────────────────────────────────────────────────────────────────────

class RoleCreate(BaseModel):
    name:        str
    description: str | None = None


class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role_id:     UUID
    name:        str
    description: str | None
    created_at:  datetime
    # True iff this role is in core.uchoice_constants.ASSIGNABLE_ROLE_NAMES --
    # the same allowlist api/admin/kefu_staff.py and api/admin/members.py
    # enforce server-side. Lets the admin panel filter its role dropdown to
    # only options that won't 400 when saved (Codex round-3 review: the UI
    # previously offered every role, including "pending", which the
    # assignable-role APIs correctly reject).
    assignable:  bool


# ── Kefu Staff ────────────────────────────────────────────────────────────────

class KefuStaffUpdate(BaseModel):
    role:           str | None = None
    is_active:      bool | None = None
    warehouse_code: str | None = None   # required if role becomes "warehouseman", enforced in the route
    display_name:   str | None = None


class KefuStaffResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    staff_id:        UUID
    open_kfid:       str
    external_userid: str
    group_id:        UUID
    role:            str
    display_name:    str | None
    warehouse_code:  str | None
    is_active:       bool
    created_at:      datetime


class GroupServiceRoleGrant(BaseModel):
    role:       str    # role name, e.g. "admin"
    created_by: str    # who granted this — manual until per-admin auth exists


class GroupServiceRoleResponse(BaseModel):
    group_id:        UUID
    service_type_id: UUID
    role:            str
    created_by:      str
    created_at:      datetime


# ── Group Services ────────────────────────────────────────────────────────────

class GroupServiceCreate(BaseModel):
    service_type_id: UUID
    workflow_id:     UUID
    config:          dict = Field(default_factory=dict)


class GroupServiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    group_id:        UUID
    service_type_id: UUID
    service_name:    str
    workflow_id:     UUID
    workflow_name:   str
    config:          dict


# ── Reference data ────────────────────────────────────────────────────────────

class ServiceTypeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    service_type_id:     UUID
    name:                str
    description:         str | None
    group_config_schema: dict
    is_active:           bool


class WorkflowStepResponse(BaseModel):
    step_order: int
    step_type:  str


class WorkflowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    workflow_id: UUID
    name:        str
    description: str | None
    steps:       list[WorkflowStepResponse]


# ── Request Logs ──────────────────────────────────────────────────────────────

class RequestLogSummary(BaseModel):
    log_id:        UUID
    serial_number: str
    wechat_openid: str
    display_name:  str | None
    group_id:      UUID | None
    service_name:  str | None
    status:        str
    created_at:    datetime
    completed_at:  datetime | None


class RequestLogDetail(RequestLogSummary):
    workflow_name:  str | None
    raw_message:    str
    parsed_input:   dict
    result:         Any
    error_detail:   str | None


# ── Sessions ──────────────────────────────────────────────────────────────────

class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id:       UUID
    wechat_openid:    str
    display_name:     str | None
    group_id:         UUID
    service_name:     str | None
    status:           str
    collected_fields: dict
    expires_at:       datetime
    created_at:       datetime
