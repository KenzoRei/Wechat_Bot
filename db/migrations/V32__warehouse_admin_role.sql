\encoding UTF8
-- ============================================================
-- V32: seed the "warehouse_admin" role
-- Logistics WeChat Bot Platform
-- Date: 2026-09-15
--
-- New warehouse-scoped role (core.role_registry.WAREHOUSE_SCOPED_ROLE_
-- NAMES, alongside "warehouseman") -- makes uchoice inbound/outbound
-- requests and confirms their own completion, scoped to whichever
-- warehouse(s) warehouse_codes assigns them (enforced the same way as
-- warehouseman: core.pre_confirm_validators._valid_caller_warehouse_scope
-- + handlers/uchoice/storage_txns.py's execution-time backstop). See
-- docs/architecture/decisions/adr-010-role-service-policy-declarations.md
-- for the policy-declaration mechanism this role exercises, and
-- core/role_registry.py's ASSIGNABLE_ROLES/WAREHOUSE_SCOPED_ROLE_NAMES for
-- the code-level allowlists that make it actually assignable.
--
-- Deliberately global (role_service_permission, not the old per-group
-- group_service_role -- see V30), matching every other role's grants.
-- Idempotent for both an existing deployment and a fresh database.
-- ============================================================

INSERT INTO role (name, description)
VALUES ('warehouse_admin', 'Makes uchoice inbound/outbound requests and confirms their completion, scoped to assigned warehouse(s)')
ON CONFLICT (name) DO NOTHING;

INSERT INTO role_service_permission (role_id, service_type_id, created_by)
SELECT r.role_id, st.service_type_id, 'migration_v32'
FROM (VALUES
    ('warehouse_admin', 'uchoice_inbound_request'),
    ('warehouse_admin', 'uchoice_outbound_request'),
    ('warehouse_admin', 'confirm_inbound_completion'),
    ('warehouse_admin', 'confirm_outbound_completion')
) AS grants(role_name, service_name)
JOIN role r ON r.name = grants.role_name
JOIN service_type st ON st.name = grants.service_name
ON CONFLICT (role_id, service_type_id) DO NOTHING;

-- role_change's own input_schema field_hints list valid new_role values in
-- prose -- keep it in sync so the AI-facing hint doesn't silently omit the
-- newest assignable role (same maintenance V22/V27 already did for this
-- same field).
UPDATE service_type
SET input_schema = jsonb_set(
    input_schema,
    '{field_hints,new_role}',
    '"One of admin, customer, warehouseman, warehouse_admin, accountant, label_agent, fedex_label_agent."'::jsonb
)
WHERE name = 'role_change';

UPDATE service_type
SET input_schema = jsonb_set(
    input_schema,
    '{field_hints,warehouse_codes}',
    '"Required only if new_role is warehouseman or warehouse_admin. One or more codes."'::jsonb
)
WHERE name = 'role_change';
