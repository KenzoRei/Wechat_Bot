"""
Read-only pre-deploy gate for core/role_registry.py policy classification
changes (ADR-010 decision 4 / the 2026-09-15 role-permission-attribute-
architecture audit, finding 3).

Granting a service to a role via the admin API is already checked
automatically at request time (api/admin/roles.py calls
core.role_policy.warehouse_grant_impact before committing a grant). But
changing WAREHOUSE_SCOPED_ROLE_NAMES itself -- adding a role to it when
that role already has existing assignments and/or service grants -- is a
source-code change with no admin-API call site to hook an automatic check
onto. This script is the documented manual step that closes that gap: run
it against the target database BEFORE merging/deploying any such change,
for every role you're about to add to (or that's already in)
WAREHOUSE_SCOPED_ROLE_NAMES, and remediate (assign warehouse_codes) any
row it reports before the deploy goes out. After the deploy,
check_warehouse_scope's fail-closed behavior means an unremediated row is
merely locked out (safe), not granted excess access -- this script exists
to avoid that surprise, not to prevent a security hole.

Usage:
    python scripts/check_role_policy_impact.py [role_name ...]

Run this AFTER editing core/role_registry.py in your working tree to add
a role to WAREHOUSE_SCOPED_ROLE_NAMES, BEFORE merging/deploying that
change. With no arguments, checks every role currently declared in
WAREHOUSE_SCOPED_ROLE_NAMES (i.e. your working tree's version, since this
script imports that module directly). Passing a role name that ISN'T
declared there is an ERROR, not a silent pass -- exit 1, no assignments
queried (2026-09-15 audit, third round: an earlier version of this script
returned exit 0 for an undeclared role like 'accountant' without ever
checking its assignments, a false pass for exactly the "role about to
become warehouse-scoped" case this script exists for). Declare the role
first, then run this.

Exits 1 if any checked role has a compliance gap OR was undeclared, so it
can be used as a CI/pre-merge gate; 0 only if every named role is both
declared and fully compliant. Read-only: issues COUNT queries only, never
writes.
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal  # noqa: E402
from core import role_policy  # noqa: E402
from core.role_registry import WAREHOUSE_SCOPED_ROLE_NAMES  # noqa: E402


def main() -> int:
    role_names = sys.argv[1:] or sorted(WAREHOUSE_SCOPED_ROLE_NAMES)
    db = SessionLocal()
    had_problem = False
    try:
        for role_name in role_names:
            try:
                gap = role_policy.warehouse_scope_compliance_gap(db, role_name)
            except ValueError as exc:
                had_problem = True
                print(f"{role_name}: ERROR -- {exc}")
                continue
            if gap is None:
                print(f"{role_name}: OK (declared warehouse-scoped, every existing holder has warehouse_codes assigned)")
                continue
            had_problem = True
            print(
                f"{role_name}: GAP -- {gap['member_count']} group member(s) and "
                f"{gap['staff_count']} Kefu staff member(s) with no warehouse_codes "
                f"assigned. Assign warehouse_codes to those rows "
                f"(PATCH /admin/groups/{{group_id}}/members/{{wechat_openid}} or "
                f"/admin/kefu-staff/{{staff_id}}) before this role's warehouse-scope "
                f"classification (or any of its warehouse-scoped service grants) goes live."
            )
    finally:
        db.close()
    return 1 if had_problem else 0


if __name__ == "__main__":
    raise SystemExit(main())
