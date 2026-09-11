-- V30: replace group_service_role (per-group role->service grants) with a
-- GLOBAL role_service_permission table.
--
-- group_service_role re-declared the same role->service mapping separately
-- per WeCom group/tenant, but a role represents a job function
-- (warehouseman, label_agent, ...) that should behave identically
-- regardless of which tenant a person belongs to -- the per-group axis
-- added no real differentiation in practice (every grant ever made in this
-- production database already targets the same single group), just
-- per-group admin busywork. group_service (which services a tenant has
-- access to AT ALL) is untouched by this change and remains the real
-- multi-tenancy boundary; a role's actual reachable services in a given
-- group are now role_service_permission INTERSECT group_service (see
-- core/access_control.py).
--
-- Backfill is a DISTINCT union across whatever groups existed -- safe even
-- if some other group's grants ever diverged from the norm: it only ever
-- widens access to the union of what was already grantable somewhere,
-- never silently revokes something a caller could already do.
--
-- Idempotent for both an existing deployment and a fresh database.

CREATE TABLE IF NOT EXISTS role_service_permission (
    role_id         UUID NOT NULL REFERENCES role(role_id) ON DELETE CASCADE,
    service_type_id UUID NOT NULL REFERENCES service_type(service_type_id) ON DELETE CASCADE,
    created_by      VARCHAR(128) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (role_id, service_type_id)
);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'group_service_role') THEN
        INSERT INTO role_service_permission (role_id, service_type_id, created_by)
        SELECT DISTINCT role_id, service_type_id, 'migrated_from_group_service_role'
        FROM group_service_role
        ON CONFLICT (role_id, service_type_id) DO NOTHING;

        DROP TABLE group_service_role;
    END IF;
END $$;
