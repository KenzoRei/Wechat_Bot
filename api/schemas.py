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
    description:         str | None = None
    is_active:           bool | None = None
    daily_request_limit: int | None = None
    context:             dict | None = None   # pass null to clear, omit to leave unchanged


class GroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    group_id:            UUID
    wechat_group_id:     str
    description:         str | None
    is_active:           bool
    daily_request_limit: int | None
    context:             dict | None
    created_at:          datetime


# ── Members ───────────────────────────────────────────────────────────────────

class MemberCreate(BaseModel):
    wechat_openid: str
    role:          str
    display_name:  str | None = None


class MemberUpdate(BaseModel):
    role:      str | None  = None
    is_active: bool | None = None


class MemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    wechat_openid: str
    group_id:      UUID
    role:          str
    display_name:  str | None
    is_active:     bool
    joined_at:     datetime


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
