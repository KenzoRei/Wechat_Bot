-- V17: bring group_service_role's per-role service scope up to the
-- reviewed target (see docs/ai-collaboration -- role-scope cross-check,
-- 2026-08-14). Two removals, twelve additions, scoped to the single
-- U-Choice group by wechat_group_id, same pattern V15 used for its own
-- grant.
--
-- Removed:
--   customer      loses confirm_inbound_completion
--   warehouseman  loses uchoice_inbound_request
--
-- Added:
--   customer      gains uchoice_outbound_request, upsert_address, view_storage_history
--   warehouseman  gains adjust_storage, confirm_outbound_completion, move_storage,
--                 recount_storage, upsert_address, view_storage_history
--   accountant    gains view_invoice, view_storage, view_storage_history
--
-- Idempotent for both an existing deployment and a fresh database.

DELETE FROM group_service_role gsr
USING role r, service_type st, group_config gc
WHERE gsr.role_id = r.role_id
  AND gsr.service_type_id = st.service_type_id
  AND gsr.group_id = gc.group_id
  AND gc.wechat_group_id = 'wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A'
  AND (
    (r.name = 'customer' AND st.name = 'confirm_inbound_completion')
    OR (r.name = 'warehouseman' AND st.name = 'uchoice_inbound_request')
  );

INSERT INTO group_service_role (group_id, service_type_id, role_id, created_by)
SELECT gc.group_id, st.service_type_id, r.role_id, 'migration_v17'
FROM (VALUES
    ('customer', 'uchoice_outbound_request'),
    ('customer', 'upsert_address'),
    ('customer', 'view_storage_history'),
    ('warehouseman', 'adjust_storage'),
    ('warehouseman', 'confirm_outbound_completion'),
    ('warehouseman', 'move_storage'),
    ('warehouseman', 'recount_storage'),
    ('warehouseman', 'upsert_address'),
    ('warehouseman', 'view_storage_history'),
    ('accountant', 'view_invoice'),
    ('accountant', 'view_storage'),
    ('accountant', 'view_storage_history')
) AS grants(role_name, service_name)
JOIN role r ON r.name = grants.role_name
JOIN service_type st ON st.name = grants.service_name
CROSS JOIN (SELECT group_id FROM group_config WHERE wechat_group_id = 'wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A') gc
ON CONFLICT (group_id, service_type_id, role_id) DO NOTHING;
