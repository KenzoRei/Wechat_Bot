\encoding UTF8
-- ============================================================
-- V27: role_change gains billing_customer_id (customer-role binding),
-- closing the gap Codex found: conversational role changes could set a
-- member to role=customer with no binding (permanently unable to create
-- labels, rejected by core.customer_directory.resolve_billing_customer_id)
-- or leave a stale binding on a member moved away from role=customer.
--
-- Mirrors the exact same "optional, conditionally required" shape already
-- used for warehouse_codes/warehouseman on this same service. Application
-- code (core/uchoice_field_sanitization.py, core/pre_confirm_validators.py,
-- handlers/uchoice/role_change.py) enforces the actual binding rules; this
-- migration only updates the catalog schema/hints so the AI knows the
-- field exists.
--
-- new_role's own hint is also corrected to include label_agent (added to
-- ASSIGNABLE_ROLE_NAMES in migration V26's companion code change but never
-- reflected here).
--
-- Idempotent for both an existing deployment and a fresh database.
-- ============================================================

UPDATE service_type
SET input_schema = '{"optional": ["warehouse_codes", "billing_customer_id"], "required": ["target_openid", "new_role"], "field_hints": {"new_role": "One of admin, customer, warehouseman, accountant, label_agent.", "target_openid": "Resolve via the injected member-list candidate list (wechat_openid + display_name + current role) against a casual name reference.", "warehouse_codes": "Required only if new_role is warehouseman. One or more codes.", "billing_customer_id": "Required only if new_role is customer -- the F###### customer this member bills to. Cleared automatically for any other role."}}'::jsonb
WHERE name = 'role_change';
