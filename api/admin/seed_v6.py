"""
One-time migration endpoint: seeds V6 data into the Render PostgreSQL.
Idempotent — safe to call multiple times (uses upsert / ON CONFLICT logic).

POST /admin/seed-v6
Header: X-Admin-Key: <admin_key>
"""
import uuid
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import text

from database import get_db
from middleware.admin_auth import verify_admin_key
from models.service import ServiceType
from models.workflow import Workflow, WorkflowStep
from models.group import GroupService

router = APIRouter(prefix="/admin", dependencies=[Depends(verify_admin_key)])

# ── Fixed UUIDs (must match V6__oms_service_type.sql) ────────────────────────
_SERVICE_OMS_ID  = uuid.UUID("a1b2c3d4-0003-0000-0000-000000000003")
_SERVICE_BASE_ID = uuid.UUID("a1b2c3d4-0001-0000-0000-000000000001")  # fedex_label
_WORKFLOW_ID     = uuid.UUID("af000001-0000-0000-0000-000000000005")
_GROUP_ID        = uuid.UUID("a81d11df-b487-410b-abdf-5126f13e4992")

_OMS_CREDS = {
    "oms_app_key":    "7067eec5f4ce4b3fa4321aabbe2623ab",
    "oms_app_secret": "0b6069240b1d49438761c3155a36ddfc",
    "oms_wh_code":    "DE19713",
}

_INPUT_SCHEMA_OMS = {
    "required": [
        "oms_outbound_order_no",
        "shipper_name", "shipper_phone",
        "shipper_street", "shipper_city", "shipper_state", "shipper_zip",
        "recipient_name", "recipient_phone",
        "recipient_street", "recipient_city", "recipient_state", "recipient_zip",
        "weight_lbs",
    ],
    "optional": [
        "service_level", "shipper_corp_name", "shipper_country",
        "recipient_corp_name", "recipient_country",
        "length_in", "width_in", "height_in", "reference_number",
    ],
    "field_hints": {
        "oms_outbound_order_no": "OMS outbound order number (e.g. OBS0162604110RV)",
        "service_level":         "e.g. PRIORITY_OVERNIGHT, FEDEX_GROUND (default)",
        "weight_lbs":            "numeric value in pounds",
        "shipper_country":       "default is US",
        "recipient_country":     "default is US",
    },
}

_GROUP_CONFIG_SCHEMA_OMS = {
    "required": ["ydd_api_key", "ydd_cust_id", "ydd_channel_id",
                 "oms_app_key", "oms_app_secret", "oms_wh_code"],
    "optional": ["ydd_account_code"],
    "field_hints": {
        "ydd_api_key":    "YiDiDa API key",
        "ydd_cust_id":    "YiDiDa customer ID",
        "ydd_channel_id": "YiDiDa channel ID",
        "oms_app_key":    "OMS App_Key from xlwms portal",
        "oms_app_secret": "OMS App_Secret from xlwms portal",
        "oms_wh_code":    "OMS warehouse code fallback (e.g. DE19713)",
    },
}

_WORKFLOW_STEPS = [
    {"step_order": 1, "step_type": "create_fedex_label",   "config": {"carrier": "fedex"}},
    {"step_order": 2, "step_type": "oms_create_workorder", "config": {}},
    {"step_order": 3, "step_type": "reply_wechat",         "config": {}},
]

_CONFIG_OMS_LABEL = {
    "ydd_api_key":    "abc12345",
    "ydd_cust_id":    "F000179",
    "ydd_channel_id": "Fedex home delivery 洛杉矶渠道",
    **_OMS_CREDS,
}


@router.post("/seed-v6")
def seed_v6(db: Session = Depends(get_db)):
    """
    Idempotent upsert of all V6 data:
      - fedex_oms_label service type
      - fedex_workorder workflow + steps
      - Test group: fedex_label updated to fedex_workorder + OMS creds
      - Test group: fedex_oms_label service added
    """
    ops = []

    # ── 1. fedex_oms_label service type ──────────────────────────────────────
    stmt = pg_insert(ServiceType).values(
        service_type_id=_SERVICE_OMS_ID,
        name="fedex_oms_label",
        description="FedEx label creation via YiDiDa, linked to an OMS outbound order",
        input_schema=_INPUT_SCHEMA_OMS,
        group_config_schema=_GROUP_CONFIG_SCHEMA_OMS,
        confirmation_note=(
            "Label will be generated and linked to your OMS outbound order. "
            "Contact admin immediately if changes are needed."
        ),
        is_active=True,
    ).on_conflict_do_update(
        index_elements=["service_type_id"],
        set_=dict(
            input_schema=_INPUT_SCHEMA_OMS,
            group_config_schema=_GROUP_CONFIG_SCHEMA_OMS,
        )
    )
    db.execute(stmt)
    ops.append("upserted service_type: fedex_oms_label")

    # ── 2. fedex_workorder workflow ───────────────────────────────────────────
    stmt = pg_insert(Workflow).values(
        workflow_id=_WORKFLOW_ID,
        name="fedex_workorder",
        description=(
            "Create FedEx label via YiDiDa, create OMS work order "
            "(linked if OMS order no. provided), reply to WeChat"
        ),
    ).on_conflict_do_update(
        index_elements=["workflow_id"],
        set_=dict(name="fedex_workorder"),
    )
    db.execute(stmt)
    ops.append("upserted workflow: fedex_workorder")

    # ── 3. Workflow steps ─────────────────────────────────────────────────────
    for step in _WORKFLOW_STEPS:
        stmt = pg_insert(WorkflowStep).values(
            workflow_id=_WORKFLOW_ID,
            step_order=step["step_order"],
            step_type=step["step_type"],
            config=step["config"],
        ).on_conflict_do_update(
            index_elements=["workflow_id", "step_order"],
            set_=dict(step_type=step["step_type"], config=step["config"]),
        )
        db.execute(stmt)
        ops.append(f"upserted step {step['step_order']}: {step['step_type']}")

    # ── 4. Test group: update fedex_label → fedex_workorder + OMS creds ──────
    result = db.execute(text("""
        UPDATE group_service
        SET workflow_id = :wf_id,
            config      = config || :oms_creds::jsonb
        WHERE group_id        = :group_id
          AND service_type_id = :svc_id
    """), {
        "wf_id":     str(_WORKFLOW_ID),
        "oms_creds": '{"oms_app_key":"7067eec5f4ce4b3fa4321aabbe2623ab",'
                     '"oms_app_secret":"0b6069240b1d49438761c3155a36ddfc",'
                     '"oms_wh_code":"DE19713"}',
        "group_id":  str(_GROUP_ID),
        "svc_id":    str(_SERVICE_BASE_ID),
    })
    ops.append(f"updated fedex_label group_service: {result.rowcount} row(s)")

    # ── 5. Test group: add fedex_oms_label service ────────────────────────────
    stmt = pg_insert(GroupService).values(
        group_id=_GROUP_ID,
        service_type_id=_SERVICE_OMS_ID,
        workflow_id=_WORKFLOW_ID,
        config=_CONFIG_OMS_LABEL,
    ).on_conflict_do_update(
        index_elements=["group_id", "service_type_id"],
        set_=dict(workflow_id=_WORKFLOW_ID, config=_CONFIG_OMS_LABEL),
    )
    db.execute(stmt)
    ops.append("upserted group_service: fedex_oms_label")

    db.commit()
    return {"status": "ok", "ops": ops}
