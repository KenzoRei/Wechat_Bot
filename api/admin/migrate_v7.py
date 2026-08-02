"""
One-time migration endpoint for V7 (role table + group_service_role).
Mirrors seed_v6.py's pattern — Render's DB has no reachable external psql
connection from this environment, so schema changes go through the app itself.

Safely re-runnable: checks information_schema before each DDL step so a
partial failure can be retried without erroring on "already exists".

POST /admin/migrate-v7
Header: X-Admin-Key: <admin_key>
"""
import traceback
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db
from middleware.admin_auth import verify_admin_key

router = APIRouter(prefix="/admin", dependencies=[Depends(verify_admin_key)])


def _table_exists(db: Session, table_name: str) -> bool:
    result = db.execute(text(
        "SELECT 1 FROM information_schema.tables WHERE table_name = :t"
    ), {"t": table_name})
    return result.first() is not None


def _column_exists(db: Session, table_name: str, column_name: str) -> bool:
    result = db.execute(text(
        "SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"
    ), {"t": table_name, "c": column_name})
    return result.first() is not None


@router.post("/migrate-v7")
def migrate_v7(db: Session = Depends(get_db)):
    ops = []
    try:
        # ── 1. role table ────────────────────────────────────────────────────
        if not _table_exists(db, "role"):
            db.execute(text("""
                CREATE TABLE role (
                    role_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    name        VARCHAR(20) NOT NULL UNIQUE,
                    description VARCHAR(200),
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """))
            ops.append("created table: role")
        else:
            ops.append("skipped: role table already exists")

        db.execute(text("""
            INSERT INTO role (name, description) VALUES
                ('admin',    'Full access to group services and admin-level actions'),
                ('customer', 'Standard requester — access limited to explicitly granted services')
            ON CONFLICT (name) DO NOTHING
        """))
        ops.append("upserted role rows: admin, customer")

        # ── 2. group_member.role -> role_id ─────────────────────────────────
        has_role_id = _column_exists(db, "group_member", "role_id")
        has_old_role = _column_exists(db, "group_member", "role")

        if not has_role_id:
            db.execute(text("ALTER TABLE group_member ADD COLUMN role_id UUID"))
            ops.append("added column: group_member.role_id")
            has_role_id = True

        if has_old_role:
            db.execute(text("""
                UPDATE group_member gm
                SET role_id = r.role_id
                FROM role r
                WHERE r.name = gm.role
                  AND gm.role_id IS NULL
            """))
            ops.append("backfilled group_member.role_id from role text")

            missing = db.execute(text(
                "SELECT count(*) FROM group_member WHERE role_id IS NULL"
            )).scalar()
            if missing:
                raise RuntimeError(f"{missing} group_member row(s) have no matching role — aborting before drop")

            # NOT NULL + FK only added once, guarded by checking constraint existence
            constraint_exists = db.execute(text("""
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name = 'group_member' AND constraint_name = 'group_member_role_id_fkey'
            """)).first() is not None

            if not constraint_exists:
                db.execute(text("ALTER TABLE group_member ALTER COLUMN role_id SET NOT NULL"))
                db.execute(text("""
                    ALTER TABLE group_member ADD CONSTRAINT group_member_role_id_fkey
                        FOREIGN KEY (role_id) REFERENCES role(role_id) ON DELETE RESTRICT
                """))
                ops.append("set role_id NOT NULL + FK constraint")

            db.execute(text("ALTER TABLE group_member DROP COLUMN role"))
            ops.append("dropped old column: group_member.role")
        else:
            ops.append("skipped: group_member.role already migrated/dropped")

        # ── 3. group_service_role table ─────────────────────────────────────
        if not _table_exists(db, "group_service_role"):
            db.execute(text("""
                CREATE TABLE group_service_role (
                    group_id        UUID NOT NULL,
                    service_type_id UUID NOT NULL,
                    role_id         UUID NOT NULL REFERENCES role(role_id) ON DELETE CASCADE,
                    created_by      VARCHAR(128) NOT NULL,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (group_id, service_type_id, role_id),
                    FOREIGN KEY (group_id, service_type_id)
                        REFERENCES group_service (group_id, service_type_id) ON DELETE CASCADE
                )
            """))
            ops.append("created table: group_service_role")
        else:
            ops.append("skipped: group_service_role table already exists")

        # ── 4. Backfill: grant admin role full access to every existing assignment ─
        db.execute(text("""
            INSERT INTO group_service_role (group_id, service_type_id, role_id, created_by)
            SELECT gs.group_id, gs.service_type_id, r.role_id, 'system_migration_v7'
            FROM group_service gs
            CROSS JOIN role r
            WHERE r.name = 'admin'
            ON CONFLICT DO NOTHING
        """))
        ops.append("granted admin role access to all existing group_service rows")

        db.commit()
        return {"status": "ok", "ops": ops}

    except Exception as e:
        db.rollback()
        return {"status": "error", "ops": ops, "error": str(e), "trace": traceback.format_exc()}
