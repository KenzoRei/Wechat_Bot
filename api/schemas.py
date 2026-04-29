"""
Pydantic request and response schemas for all API endpoints.
FastAPI uses these for automatic request validation and response serialization.
"""
from pydantic import BaseModel, Field
from uuid import UUID
from typing import Any


# ── Groups ────────────────────────────────────────────────────────────────────

class GroupCreate(BaseModel):
    wechat_group_id:     str
    description:         str | None = None
    daily_request_limit: int | None = None


class GroupUpdate(BaseModel):
    description:         str | None = None
    is_active:           bool | None = None
    daily_request_limit: int | None = None


class GroupResponse(BaseModel):
    group_id:            UUID
    wechat_group_id:     str
    description:         str | None
    is_active:           bool
    daily_request_limit: int | None
    created_at:          str

    class Config:
        from_attributes = True


# ── Members ───────────────────────────────────────────────────────────────────

class MemberCreate(BaseModel):
    wechat_openid: str
    role:          str      # "admin" or "customer"
    display_name:  str | None = None


class MemberUpdate(BaseModel):
    role:      str | None  = None
    is_active: bool | None = None


class MemberResponse(BaseModel):
    wechat_openid: str
    group_id:      UUID
    role:          str
    display_name:  str | None
    is_active:     bool
    joined_at:     str

    class Config:
        from_attributes = True


# ── Group Services ────────────────────────────────────────────────────────────

class GroupServiceCreate(BaseModel):
    service_type_id: UUID
    workflow_id:     UUID
    config:          dict = Field(default_factory=dict)


class GroupServiceResponse(BaseModel):
    group_id:        UUID
    service_type_id: UUID
    service_name:    str
    workflow_id:     UUID
    workflow_name:   str
    config:          dict

    class Config:
        from_attributes = True


# ── Reference data ────────────────────────────────────────────────────────────

class ServiceTypeResponse(BaseModel):
    service_type_id:     UUID
    name:                str
    description:         str | None
    group_config_schema: dict
    is_active:           bool

    class Config:
        from_attributes = True


class WorkflowStepResponse(BaseModel):
    step_order: int
    step_type:  str


class WorkflowResponse(BaseModel):
    workflow_id: UUID
    name:        str
    description: str | None
    steps:       list[WorkflowStepResponse]

    class Config:
        from_attributes = True


# ── Request Logs ──────────────────────────────────────────────────────────────

class RequestLogSummary(BaseModel):
    log_id:        UUID
    serial_number: str
    wechat_openid: str
    display_name:  str | None   # joined from group_member
    group_id:      UUID | None
    service_name:  str | None   # joined from service_type
    status:        str
    created_at:    str
    completed_at:  str | None


class RequestLogDetail(RequestLogSummary):
    workflow_name:  str | None  # joined from workflow via group_service
    raw_message:    str
    parsed_input:   dict
    result:         Any
    error_detail:   str | None


# ── Sessions ──────────────────────────────────────────────────────────────────

class SessionResponse(BaseModel):
    session_id:       UUID
    wechat_openid:    str
    display_name:     str | None   # joined from group_member
    group_id:         UUID
    service_name:     str | None   # joined from service_type
    status:           str
    collected_fields: dict
    expires_at:       str
    created_at:       str

    class Config:
        from_attributes = True
