"""
One-time migration endpoint: seeds V6 data into the Render PostgreSQL.
Idempotent — uses raw SQL with ON CONFLICT so it's safe to call multiple times.

POST /admin/seed-v6
Header: X-Admin-Key: <admin_key>
"""
import traceback
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db
from middleware.admin_auth import verify_admin_key

router = APIRouter(prefix="/admin", dependencies=[Depends(verify_admin_key)])


@router.post("/seed-v6")
def seed_v6(db: Session = Depends(get_db)):
    """Idempotent upsert of all V6 OMS data."""
    ops = []
    try:
        # ── 1. fedex_oms_label service type ──────────────────────────────────
        db.execute(text("""
            INSERT INTO service_type (
                service_type_id, name, description,
                input_schema, group_config_schema, confirmation_note, is_active
            ) VALUES (
                'a1b2c3d4-0003-0000-0000-000000000003',
                'fedex_oms_label',
                'FedEx label creation via YiDiDa, linked to an OMS outbound order',
                :input_schema,
                :group_config_schema,
                'Label will be generated and linked to your OMS outbound order. Contact admin immediately if changes are needed.',
                true
            )
            ON CONFLICT (service_type_id) DO UPDATE SET
                name                = EXCLUDED.name,
                description         = EXCLUDED.description,
                input_schema        = EXCLUDED.input_schema,
                group_config_schema = EXCLUDED.group_config_schema,
                confirmation_note   = EXCLUDED.confirmation_note
        """), {
            "input_schema": """{
                "required": ["oms_outbound_order_no",
                    "shipper_name","shipper_phone","shipper_street","shipper_city","shipper_state","shipper_zip",
                    "recipient_name","recipient_phone","recipient_street","recipient_city","recipient_state","recipient_zip",
                    "weight_lbs"],
                "optional": ["service_level","shipper_corp_name","shipper_country",
                    "recipient_corp_name","recipient_country","length_in","width_in","height_in","reference_number"],
                "field_hints": {
                    "oms_outbound_order_no": "OMS outbound order number (e.g. OBS0162604110RV)",
                    "service_level": "e.g. PRIORITY_OVERNIGHT, FEDEX_GROUND (default)",
                    "weight_lbs": "numeric value in pounds",
                    "shipper_country": "default is US",
                    "recipient_country": "default is US"
                }
            }""",
            "group_config_schema": """{
                "required": ["ydd_api_key","ydd_cust_id","ydd_channel_id",
                    "oms_app_key","oms_app_secret","oms_wh_code"],
                "optional": ["ydd_account_code"],
                "field_hints": {
                    "ydd_api_key":    "YiDiDa API key",
                    "ydd_cust_id":    "YiDiDa customer ID",
                    "ydd_channel_id": "YiDiDa channel ID",
                    "oms_app_key":    "OMS App_Key from xlwms portal",
                    "oms_app_secret": "OMS App_Secret from xlwms portal",
                    "oms_wh_code":    "OMS warehouse code fallback (e.g. DE19713)"
                }
            }""",
        })
        ops.append("upserted service_type: fedex_oms_label")

        # ── 2. fedex_workorder workflow ───────────────────────────────────────
        db.execute(text("""
            INSERT INTO workflow (workflow_id, name, description)
            VALUES (
                'af000001-0000-0000-0000-000000000005',
                'fedex_workorder',
                'Create FedEx label via YiDiDa, create OMS work order (linked if OMS order no. provided), reply to WeChat'
            )
            ON CONFLICT (workflow_id) DO UPDATE SET
                name        = EXCLUDED.name,
                description = EXCLUDED.description
        """))
        ops.append("upserted workflow: fedex_workorder")

        # ── 3. Workflow steps ─────────────────────────────────────────────────
        steps = [
            (1, 'create_fedex_label',   '{"carrier": "fedex"}'),
            (2, 'oms_create_workorder', '{}'),
            (3, 'reply_wechat',         '{}'),
        ]
        for order, step_type, config in steps:
            db.execute(text("""
                INSERT INTO workflow_step (workflow_id, step_order, step_type, config)
                VALUES (
                    'af000001-0000-0000-0000-000000000005',
                    :order, :step_type, :config::jsonb
                )
                ON CONFLICT (workflow_id, step_order) DO UPDATE SET
                    step_type = EXCLUDED.step_type,
                    config    = EXCLUDED.config
            """), {"order": order, "step_type": step_type, "config": config})
            ops.append(f"upserted step {order}: {step_type}")

        # ── 4. Test group: update fedex_label service ─────────────────────────
        result = db.execute(text("""
            UPDATE group_service
            SET workflow_id = 'af000001-0000-0000-0000-000000000005',
                config      = config || '{"oms_app_key":"7067eec5f4ce4b3fa4321aabbe2623ab",
                                          "oms_app_secret":"0b6069240b1d49438761c3155a36ddfc",
                                          "oms_wh_code":"DE19713"}'::jsonb
            WHERE group_id        = 'a81d11df-b487-410b-abdf-5126f13e4992'
              AND service_type_id = 'a1b2c3d4-0001-0000-0000-000000000001'
        """))
        ops.append(f"updated fedex_label group_service: {result.rowcount} row(s)")

        # ── 5. Test group: add fedex_oms_label service ────────────────────────
        db.execute(text("""
            INSERT INTO group_service (group_id, service_type_id, workflow_id, config)
            VALUES (
                'a81d11df-b487-410b-abdf-5126f13e4992',
                'a1b2c3d4-0003-0000-0000-000000000003',
                'af000001-0000-0000-0000-000000000005',
                '{"ydd_api_key":"abc12345","ydd_cust_id":"F000179",
                  "ydd_channel_id":"Fedex home delivery 洛杉矶渠道",
                  "oms_app_key":"7067eec5f4ce4b3fa4321aabbe2623ab",
                  "oms_app_secret":"0b6069240b1d49438761c3155a36ddfc",
                  "oms_wh_code":"DE19713"}'::jsonb
            )
            ON CONFLICT (group_id, service_type_id) DO UPDATE SET
                workflow_id = EXCLUDED.workflow_id,
                config      = EXCLUDED.config
        """))
        ops.append("upserted group_service: fedex_oms_label")

        db.commit()
        return {"status": "ok", "ops": ops}

    except Exception as e:
        db.rollback()
        return {"status": "error", "ops": ops, "error": str(e), "trace": traceback.format_exc()}
