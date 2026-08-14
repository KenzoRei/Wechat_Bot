from sqlalchemy import String, Text, Boolean, Integer, ForeignKey, DateTime, CheckConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from database import Base
import uuid
from datetime import datetime


class UchoiceCustomer(Base):
    """
    Real customer-directory entity, promoted from what was previously
    implicit in uchoice_address.company_name. Includes non-customer-specific
    entries like "JFK Warehouse"/
    "散客" as real rows; there is no global or unscoped address category.
    """
    __tablename__ = "uchoice_customer"

    customer_id:    Mapped[uuid.UUID]  = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    customer_code:  Mapped[str]        = mapped_column(String(30), nullable=False, unique=True)
    canonical_name: Mapped[str]        = mapped_column(String(200), nullable=False)
    aliases:        Mapped[list[str]]  = mapped_column(ARRAY(Text), nullable=False, server_default=text("'{}'"))
    is_active:      Mapped[bool]       = mapped_column(Boolean, nullable=False, default=True)
    created_at:     Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at:     Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class KefuStaff(Base):
    """
    A staff member's Kefu identity. Reuses the self-registration pattern
    (pending role, admin
    promotes) -- not the group_member table, which is WeCom-group-shaped
    and doesn't fit Kefu's 1:1 model. group_id is fixed per open_kfid at
    deployment time, never chosen by staff or inferred from a message.
    """
    __tablename__ = "kefu_staff"

    staff_id:       Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    open_kfid:      Mapped[str]            = mapped_column(String(128), nullable=False)
    external_userid: Mapped[str]           = mapped_column(String(128), nullable=False)
    group_id:       Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), ForeignKey("group_config.group_id", ondelete="RESTRICT"), nullable=False)
    role_id:        Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), ForeignKey("role.role_id", ondelete="RESTRICT"), nullable=False)
    warehouse_code: Mapped[str | None]     = mapped_column(String(20))
    display_name:   Mapped[str | None]     = mapped_column(String(200))
    is_active:      Mapped[bool]           = mapped_column(Boolean, nullable=False, default=True)
    created_at:     Mapped[datetime]       = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at:     Mapped[datetime]       = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class CaseTurn(Base):
    """
    Durable per-turn actor audit. Distinct
    from interaction_log, which keeps its own existing funnel/efficiency
    purpose. source_message_id's uniqueness is what makes a duplicate Kefu
    msgid answerable from stored data instead of re-running extraction,
    mutation, or execution; reply_text
    etc. are persisted here specifically so that replay is possible.
    """
    __tablename__ = "case_turn"
    __table_args__ = (
        CheckConstraint(
            "(role = 'user' AND num_nonnulls(acting_staff_id, acting_wechat_openid) = 1) "
            "OR (role = 'assistant' AND acting_staff_id IS NULL AND acting_wechat_openid IS NULL)",
            name="ck_case_turn_actor",
        ),
    )

    turn_id:              Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    session_id:            Mapped[uuid.UUID]     = mapped_column(UUID(as_uuid=True), ForeignKey("conversation_session.session_id", ondelete="CASCADE"), nullable=False)
    case_revision:          Mapped[int]          = mapped_column(Integer, nullable=False)
    acting_staff_id:        Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("kefu_staff.staff_id"))
    acting_wechat_openid:   Mapped[str | None]   = mapped_column(String(128))
    role:                   Mapped[str]          = mapped_column(String(20), nullable=False)
    source_message_id:      Mapped[str | None]   = mapped_column(String(128), unique=True)
    reply_text:              Mapped[str | None]  = mapped_column(Text)
    customer_copy_text:       Mapped[str | None] = mapped_column(Text)
    artifact_keys:             Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    content:                    Mapped[str]      = mapped_column(Text, nullable=False)
    created_at:                  Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class CaseExecution(Base):
    """
    Durable execution ledger. Recovery
    distinguishes what's actually provable: db_committed_at is only ever
    set in the same transaction as the business DB mutation it certifies, so
    a claim that never
    reached db_committed can be safely retried, while one that did must
    only ever resume the post-commit side-effect phase, never re-run the
    DB mutation.
    """
    __tablename__ = "case_execution"
    __table_args__ = (
        CheckConstraint("status IN ('claimed', 'db_committed', 'completed', 'failed')", name="ck_case_execution_status"),
    )

    execution_id:     Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    # Nullable because a brand-new case has no session when first claimed;
    # populated once the session becomes known (V12 migration).
    session_id:       Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("conversation_session.session_id", ondelete="CASCADE"))
    execution_key:    Mapped[str]            = mapped_column(String(128), nullable=False, unique=True)
    status:           Mapped[str]            = mapped_column(String(20), nullable=False, default="claimed")
    claimed_by:       Mapped[str]            = mapped_column(String(128), nullable=False)
    claimed_at:       Mapped[datetime]       = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    lease_expires_at: Mapped[datetime]       = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at:     Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    db_committed_at:  Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at:     Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error:       Mapped[str | None]     = mapped_column(Text)


class KefuStaffCaseContext(Base):
    """
    Staff-to-current-case binding. Lets an unqualified follow-up continue the
    case a staff member is currently bound to, without repeating the case
    number every turn. customer_id is never copied here -- it stays
    locked on the case itself (conversation_session.customer_id) so there
    is only one place it can live.
    """
    __tablename__ = "kefu_staff_case_context"

    staff_id:          Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), ForeignKey("kefu_staff.staff_id", ondelete="CASCADE"), primary_key=True)
    active_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("conversation_session.session_id", ondelete="SET NULL"))
    updated_at:        Mapped[datetime]        = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class KefuSyncCursor(Base):
    """
    Per-kf-account sync_msg cursor used by the single-writer receiver/worker.
    """
    __tablename__ = "kefu_sync_cursor"

    open_kfid:  Mapped[str]      = mapped_column(String(128), primary_key=True)
    cursor:     Mapped[str]      = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class KefuInboundMessage(Base):
    """
    Durable inbound message queue with lease-based claim recovery and
    serialized per-identity claims.
    """
    __tablename__ = "kefu_inbound_message"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'claimed', 'processed', 'failed')", name="ck_kefu_inbound_message_status"),
    )

    msgid:            Mapped[str]            = mapped_column(String(128), primary_key=True)
    open_kfid:        Mapped[str]            = mapped_column(String(128), nullable=False)
    external_userid:  Mapped[str]            = mapped_column(String(128), nullable=False)
    payload:          Mapped[dict]           = mapped_column(JSONB, nullable=False)
    received_at:      Mapped[datetime]       = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    processed_at:     Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status:           Mapped[str]            = mapped_column(String(20), nullable=False, default="pending")
    claimed_by:       Mapped[str | None]     = mapped_column(String(128))
    claimed_at:       Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count:    Mapped[int]            = mapped_column(Integer, nullable=False, default=0)
    last_error:       Mapped[str | None]     = mapped_column(Text)


class KefuOutboundDelivery(Base):
    """
    Durable outbound delivery tracking.
    recipient_staff_id is immutable at creation time -- never derived from
    session.opened_by_staff_id at send time, since any authorized staff
    member may produce a given response. text_content/artifact_* give deferred
    (window-closed)
    deliveries a durable payload that survives a process restart -- a file
    delivery is regenerated on demand from (request_log_id, doc_type)
    rather than storing raw bytes, preserving PDF-generation idempotency.
    """
    __tablename__ = "kefu_outbound_delivery"
    __table_args__ = (
        CheckConstraint("num_nonnulls(session_id, request_log_id) = 1", name="ck_kefu_outbound_delivery_target_xor"),
        CheckConstraint(
            "(payload_type = 'text' AND text_content IS NOT NULL AND artifact_request_log_id IS NULL) "
            "OR (payload_type = 'file' AND text_content IS NULL "
            "AND artifact_request_log_id IS NOT NULL AND artifact_doc_type IS NOT NULL AND artifact_key IS NOT NULL)",
            name="ck_kefu_outbound_delivery_payload",
        ),
        CheckConstraint("payload_type IN ('text', 'file')", name="ck_kefu_outbound_delivery_payload_type"),
        CheckConstraint("status IN ('pending', 'sent', 'failed')", name="ck_kefu_outbound_delivery_status"),
    )

    delivery_id:              Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    session_id:                Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("conversation_session.session_id"))
    request_log_id:             Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("request_log.log_id"))
    recipient_staff_id:          Mapped[uuid.UUID]     = mapped_column(UUID(as_uuid=True), ForeignKey("kefu_staff.staff_id"), nullable=False)
    idempotency_key:              Mapped[str]          = mapped_column(String(128), nullable=False, unique=True)
    payload_type:                  Mapped[str]         = mapped_column(String(10), nullable=False)
    text_content:                   Mapped[str | None] = mapped_column(Text)
    artifact_request_log_id:         Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("request_log.log_id"))
    artifact_doc_type:                Mapped[str | None] = mapped_column(String(50))
    artifact_key:                      Mapped[str | None] = mapped_column(String(200))
    payload_hash:                       Mapped[str]     = mapped_column(String(64), nullable=False)
    provider_message_id:                 Mapped[str | None] = mapped_column(String(128))
    status:                               Mapped[str]   = mapped_column(String(20), nullable=False, default="pending")
    attempt_count:                        Mapped[int]   = mapped_column(Integer, nullable=False, default=0)
    next_retry_at:                        Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at:                              Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error:                           Mapped[str | None] = mapped_column(Text)
    created_at:                           Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at:                           Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
