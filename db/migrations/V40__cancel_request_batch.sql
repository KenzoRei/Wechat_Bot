\encoding UTF8
-- ============================================================
-- V40: cancel_inbound_request_batch / cancel_outbound_request_batch
-- Logistics WeChat Bot Platform
-- Date: 2026-10-08
--
-- Lets a caller cancel several processing requests at once, all-or-nothing.
-- Kefu only: the selection and execution machinery lives in
-- core/completion_batch.py (shared family table), core/kefu_turn_apply.py
-- and handlers/uchoice/cancel_batch.py, and core/access_control.py hides
-- both services from Smart Robot. Design:
-- docs/ai-collaboration/2026-10-cancel-batch/plan.md.
--
-- Same shape as V33 (batch completion): targets_existing_request = FALSE
-- (the batch case owns a placeholder request_log and references its targets
-- through collected_fields.reference_serials), and the AI only ever
-- proposes a `selection` (hence required: []).
--
-- Reruns preserve revocations (plan review R2). ON CONFLICT DO NOTHING only
-- skips grants that still exist; a grant an admin deleted would be inserted
-- again. So group enablement and role grants are added ONLY for batch
-- service types this run itself creates, recorded before anything is
-- inserted. A rerun (possible only if schema_migrations is lost or reset)
-- refreshes the definitions but grants nothing and enables no group.
-- scripts/apply_migrations.py runs this file and its ledger row in one
-- transaction, so it is never half-applied; it must contain no COMMIT.
-- ============================================================

CREATE TEMP TABLE v40_new_batch_services ON COMMIT DROP AS
SELECT pair.batch_name
FROM (VALUES ('cancel_inbound_request_batch'), ('cancel_outbound_request_batch')) AS pair(batch_name)
WHERE NOT EXISTS (SELECT 1 FROM service_type st WHERE st.name = pair.batch_name);

INSERT INTO service_type (
    service_type_id, name, description, input_schema, group_config_schema,
    confirmation_note, is_active, requires_confirmation,
    targets_existing_request, awaits_completion, keywords
) VALUES (
    'c1000000-0000-0000-0000-000000000014',
    'cancel_inbound_request_batch',
    '一次取消多笔已确认、但仓库尚未收货确认的入库申请（全部成功或全部不执行）。用于"全部取消入库""1和3都取消""取消 REQ-A 和 REQ-B 入库"这类一次选择两笔及以上的说法；只取消一笔时用 cancel_inbound_request。',
    '{"required": [], "optional": ["selection"], "field_hints": {"selection": "Which cancellable inbound requests to cancel, as an object: {\"select_all\": true} for 全部/所有; {\"indices\": [1,3]} for positions in the numbered list the user was just shown; {\"serials\": [\"REQ-...\"]} for explicit serial numbers (a unique suffix like \"086\" is fine); {\"exclude_indices\": [2]} / {\"exclude_serials\": [...]} for 除了…. Never invent serials."}}'::jsonb,
    '{}'::jsonb,
    '取消后无法恢复，如需重新入库请重新提交申请。',
    true, true, false, false,
    '["批量取消入库", "全部取消入库", "一起取消入库"]'::jsonb
)
ON CONFLICT (name) DO UPDATE
SET description = EXCLUDED.description,
    input_schema = EXCLUDED.input_schema,
    confirmation_note = EXCLUDED.confirmation_note,
    requires_confirmation = EXCLUDED.requires_confirmation,
    targets_existing_request = EXCLUDED.targets_existing_request,
    awaits_completion = EXCLUDED.awaits_completion,
    keywords = EXCLUDED.keywords,
    is_active = true;

INSERT INTO service_type (
    service_type_id, name, description, input_schema, group_config_schema,
    confirmation_note, is_active, requires_confirmation,
    targets_existing_request, awaits_completion, keywords
) VALUES (
    'c1000000-0000-0000-0000-000000000015',
    'cancel_outbound_request_batch',
    '一次取消多笔已确认、但仓库尚未发货确认的出库申请（全部成功或全部不执行）。用于"全部取消出库""1和3都取消""取消 REQ-A 和 REQ-B 出库"这类一次选择两笔及以上的说法；只取消一笔时用 cancel_outbound_request。',
    '{"required": [], "optional": ["selection"], "field_hints": {"selection": "Which cancellable outbound requests to cancel, as an object: {\"select_all\": true} for 全部/所有; {\"indices\": [1,3]} for positions in the numbered list the user was just shown; {\"serials\": [\"REQ-...\"]} for explicit serial numbers (a unique suffix like \"086\" is fine); {\"exclude_indices\": [2]} / {\"exclude_serials\": [...]} for 除了…. Never invent serials."}}'::jsonb,
    '{}'::jsonb,
    '取消后无法恢复，如需重新出库请重新提交申请。',
    true, true, false, false,
    '["批量取消出库", "全部取消出库", "一起取消出库"]'::jsonb
)
ON CONFLICT (name) DO UPDATE
SET description = EXCLUDED.description,
    input_schema = EXCLUDED.input_schema,
    confirmation_note = EXCLUDED.confirmation_note,
    requires_confirmation = EXCLUDED.requires_confirmation,
    targets_existing_request = EXCLUDED.targets_existing_request,
    awaits_completion = EXCLUDED.awaits_completion,
    keywords = EXCLUDED.keywords,
    is_active = true;

INSERT INTO workflow (workflow_id, name, description)
VALUES (
    'c2000000-0000-0000-0000-000000000014',
    'cancel_inbound_request_batch',
    'Lock and re-check every target in log_id order, cancel each (all-or-nothing, savepoint), notify each requester (isolated), reply'
)
ON CONFLICT (workflow_id) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO workflow (workflow_id, name, description)
VALUES (
    'c2000000-0000-0000-0000-000000000015',
    'cancel_outbound_request_batch',
    'Lock and re-check every target in log_id order, cancel each (all-or-nothing, savepoint), notify each requester (isolated), reply'
)
ON CONFLICT (workflow_id) DO UPDATE SET description = EXCLUDED.description;

DELETE FROM workflow_step WHERE workflow_id = 'c2000000-0000-0000-0000-000000000014';
INSERT INTO workflow_step (workflow_id, step_order, step_type, config) VALUES
    ('c2000000-0000-0000-0000-000000000014', 1, 'run_cancellation_batch', '{"direction": "inbound"}'::jsonb),
    ('c2000000-0000-0000-0000-000000000014', 2, 'reply_wechat', '{}'::jsonb);

DELETE FROM workflow_step WHERE workflow_id = 'c2000000-0000-0000-0000-000000000015';
INSERT INTO workflow_step (workflow_id, step_order, step_type, config) VALUES
    ('c2000000-0000-0000-0000-000000000015', 1, 'run_cancellation_batch', '{"direction": "outbound"}'::jsonb),
    ('c2000000-0000-0000-0000-000000000015', 2, 'reply_wechat', '{}'::jsonb);

-- Enabled wherever the matching single cancel service is enabled -- only
-- for batch types created by this run (see header).
INSERT INTO group_service (group_id, service_type_id, workflow_id, config)
SELECT gs.group_id, batch.service_type_id, wf.workflow_id, '{}'::jsonb
FROM (VALUES
    ('cancel_inbound_request', 'cancel_inbound_request_batch'),
    ('cancel_outbound_request', 'cancel_outbound_request_batch')
) AS pair(single_name, batch_name)
JOIN v40_new_batch_services fresh ON fresh.batch_name = pair.batch_name
JOIN service_type single ON single.name = pair.single_name
JOIN service_type batch ON batch.name = pair.batch_name
JOIN workflow wf ON wf.name = pair.batch_name
JOIN group_service gs ON gs.service_type_id = single.service_type_id
ON CONFLICT (group_id, service_type_id) DO NOTHING;

-- Same roles as the single cancel service (global grants, see V30) -- only
-- for batch types created by this run (see header).
INSERT INTO role_service_permission (role_id, service_type_id, created_by)
SELECT rsp.role_id, batch.service_type_id, 'migration_v40'
FROM (VALUES
    ('cancel_inbound_request', 'cancel_inbound_request_batch'),
    ('cancel_outbound_request', 'cancel_outbound_request_batch')
) AS pair(single_name, batch_name)
JOIN v40_new_batch_services fresh ON fresh.batch_name = pair.batch_name
JOIN service_type single ON single.name = pair.single_name
JOIN service_type batch ON batch.name = pair.batch_name
JOIN role_service_permission rsp ON rsp.service_type_id = single.service_type_id
ON CONFLICT (role_id, service_type_id) DO NOTHING;
