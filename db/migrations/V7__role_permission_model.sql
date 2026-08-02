\encoding UTF8
-- ============================================================
-- V7: Role catalog + deny-by-default service permission model
-- Logistics WeChat Bot Platform
-- Date: 2026-05-13
--
-- Replaces the hardcoded VALID_ROLES set (api/admin/members.py) with a real
-- `role` table, and adds `group_service_role` to gate which roles can see
-- which services within a group.
--
-- Permission model: DENY BY DEFAULT.
-- A (group_id, service_type_id) is invisible to a role unless a matching
-- row exists in group_service_role. To keep every existing group_service
-- assignment working after this migration, the admin role is granted
-- access to every group_service row that currently exists (see step 5).
-- ============================================================


-- ── 1. Role catalog ────────────────────────────────────────────────────────────

CREATE TABLE role (
    role_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(20) NOT NULL UNIQUE,
    description VARCHAR(200),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO role (name, description) VALUES
    ('admin',    'Full access to group services and admin-level actions'),
    ('customer', 'Standard requester — access limited to explicitly granted services')
ON CONFLICT (name) DO NOTHING;


-- ── 2. Migrate group_member.role (varchar) -> role_id (FK) ────────────────────

ALTER TABLE group_member ADD COLUMN role_id UUID;

UPDATE group_member gm
SET role_id = r.role_id
FROM role r
WHERE r.name = gm.role;

-- Fails loudly if any group_member.role value didn't match a seeded role name
-- (only 'admin'/'customer' have ever been used, so this should never fire).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM group_member WHERE role_id IS NULL) THEN
        RAISE EXCEPTION 'group_member has role values with no matching role row — backfill failed';
    END IF;
END $$;

ALTER TABLE group_member ALTER COLUMN role_id SET NOT NULL;
ALTER TABLE group_member ADD CONSTRAINT group_member_role_id_fkey
    FOREIGN KEY (role_id) REFERENCES role(role_id) ON DELETE RESTRICT;
ALTER TABLE group_member DROP COLUMN role;


-- ── 3. Service permission grants ───────────────────────────────────────────────

CREATE TABLE group_service_role (
    group_id        UUID NOT NULL,
    service_type_id UUID NOT NULL,
    role_id         UUID NOT NULL REFERENCES role(role_id) ON DELETE CASCADE,
    created_by      VARCHAR(128) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (group_id, service_type_id, role_id),
    FOREIGN KEY (group_id, service_type_id)
        REFERENCES group_service (group_id, service_type_id) ON DELETE CASCADE
);


-- ── 4. Backfill: grant admin role full access to every existing assignment ────
-- Without this, every group_service row goes dark for everyone (deny-by-default)
-- the moment this migration runs, including your own admin test account.

INSERT INTO group_service_role (group_id, service_type_id, role_id, created_by)
SELECT gs.group_id, gs.service_type_id, r.role_id, 'system_migration_v7'
FROM group_service gs
CROSS JOIN role r
WHERE r.name = 'admin'
ON CONFLICT DO NOTHING;
-- ============================================================
