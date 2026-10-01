"""
Who created each pending request, for the Kefu pending list's 创建 line
(core/uchoice_context._creator_names / _display_fields): the Kefu staff
member for a Kefu request, the group member for a group-chat one, nothing
when there's no display name. Real Postgres DB; every row created here is
removed by exact id.
"""
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from database import SessionLocal
from models.group import GroupConfig, GroupMember
from models.kefu import KefuStaff
from models.request_log import RequestLog
from models.role import Role


@pytest.fixture
def rows():
    db = SessionLocal()
    group = db.query(GroupConfig).order_by(GroupConfig.created_at).first()
    role = db.query(Role).filter_by(name="warehouseman").one()
    staff = KefuStaff(open_kfid=f"kf-cn-{uuid.uuid4().hex[:8]}", external_userid=f"st-cn-{uuid.uuid4().hex[:8]}",
                      group_id=group.group_id, role_id=role.role_id, display_name="Lily", warehouse_codes=["JFK"])
    nameless = KefuStaff(open_kfid=f"kf-cn-{uuid.uuid4().hex[:8]}", external_userid=f"st-cn-{uuid.uuid4().hex[:8]}",
                         group_id=group.group_id, role_id=role.role_id, display_name=None, warehouse_codes=["JFK"])
    openid = f"wx-cn-{uuid.uuid4().hex[:8]}"
    member = GroupMember(wechat_openid=openid, group_id=group.group_id, role_id=role.role_id,
                         display_name="Simon", warehouse_codes=["JFK"])
    db.add_all([staff, nameless, member])
    db.flush()
    logs = [
        RequestLog(group_id=group.group_id, status="processing", raw_message="cn", source_channel="kefu",
                   submitted_by_staff_id=staff.staff_id),
        RequestLog(group_id=group.group_id, status="processing", raw_message="cn", source_channel="smart_robot",
                   wechat_openid=openid),
        RequestLog(group_id=group.group_id, status="processing", raw_message="cn", source_channel="kefu",
                   submitted_by_staff_id=nameless.staff_id),
    ]
    db.add_all(logs)
    db.commit()
    yield db, logs
    db.rollback()
    for log in logs:
        db.execute(text("delete from request_log where log_id = :i"), {"i": log.log_id})
    db.execute(text("delete from kefu_staff where staff_id = any(:ids)"), {"ids": [staff.staff_id, nameless.staff_id]})
    db.execute(text("delete from group_member where wechat_openid = :o"), {"o": openid})
    db.commit()
    db.close()


def test_creator_names_by_channel(rows):
    from core.uchoice_context import _creator_names

    db, logs = rows
    names = _creator_names(db, logs)
    assert names[logs[0].log_id] == "Lily"
    assert names[logs[1].log_id] == "Simon"
    assert logs[2].log_id not in names          # no display name: left out, never an ID


def test_display_fields_destination_and_units(rows):
    from core.uchoice_context import _display_fields

    db, logs = rows
    fields = _display_fields(
        logs[0],
        {"sku_lines": [{"sku_code": "t1", "pallet_count": 1}, {"sku_code": "s3", "box_count": 2},
                       {"sku_code": "t1", "pallet_count": 2}]},
        SimpleNamespace(company_name="散客", addr=""),
        {"t1": "T1 3-inch Clear Packing Tape", "s3": "S3 Black Stretch Wrap"},
        {logs[0].log_id: "Lily"},
    )
    assert fields["sku_lines_display"] == ["T1 3-inch Clear Packing Tape ×3托", "S3 Black Stretch Wrap ×2箱（散）"]
    assert fields["destination_name"] == "散客" and "destination_address" not in fields
    assert fields["created_by_name"] == "Lily"
    bare_address = _display_fields(logs[1], {}, SimpleNamespace(company_name=None, addr="14502 156th St"), {}, {})
    assert bare_address["destination_name"] == "14502 156th St"
