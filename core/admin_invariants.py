"""
Cross-channel administrative invariants shared by every place a member's
role or active status can change: the conversational role_change service
(core/pre_confirm_validators.py's _last_admin_protection) and the REST admin
APIs (api/admin/members.py and api/admin/kefu_staff.py). The invariant is
cross-channel because admin is assignable through either channel.
"""
from sqlalchemy import text
from sqlalchemy.orm import Session as DBSession

from models.group import GroupMember
from models.kefu import KefuStaff
from models.role import Role


def lock_group_admin_invariant(db: DBSession, group_id) -> None:
    """
    Transaction-scoped PostgreSQL advisory lock serializing last-admin checks
    for one group. Without this, two concurrent requests, such as one against
    api/admin/members.py and one
    against api/admin/kefu_staff.py -- can each count 2 admins before either
    commits, both independently decide a demotion/deactivation is safe, and
    the group ends up with zero). Callers must acquire this BEFORE counting
    and hold it through their own commit/rollback -- it releases
    automatically at end of transaction (pg_advisory_xact_lock), same
    mechanism core/kefu_delivery.py's deliver_one already uses for a
    different per-key serialization.

    Different group_ids never block each other; this only serializes
    concurrent requests targeting the SAME group.
    """
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"admin_invariant:{group_id}"},
    )


def count_active_admins(db: DBSession, group_id) -> int:
    admin_role = db.query(Role).filter_by(name="admin").first()
    if not admin_role:
        return 0
    smart_robot_count = db.query(GroupMember).filter_by(
        group_id=group_id, role_id=admin_role.role_id, is_active=True
    ).count()
    kefu_count = db.query(KefuStaff).filter_by(
        group_id=group_id, role_id=admin_role.role_id, is_active=True
    ).count()
    return smart_robot_count + kefu_count


def would_remove_last_admin(
    db: DBSession,
    group_id,
    *,
    is_currently_active_admin: bool,
    new_role_name: str | None,
    new_is_active: bool | None,
) -> bool:
    """
    True if this update would take the group's last remaining active admin
    below one. Only meaningful when the target is CURRENTLY an active admin
    -- promoting someone new, or changing a non-admin's role/status, never
    trips this.
    """
    if not is_currently_active_admin:
        return False

    stays_active = new_is_active if new_is_active is not None else True
    stays_admin = new_role_name is None or new_role_name == "admin"
    if stays_active and stays_admin:
        return False  # no change to admin-ness

    return count_active_admins(db, group_id) <= 1
