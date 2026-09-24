\encoding UTF8
-- ============================================================
-- V33: confirm_inbound_completion_batch / confirm_outbound_completion_batch
-- Logistics WeChat Bot Platform
-- Date: 2026-09-24
--
-- Lets a warehouse caller confirm several processing requests at once,
-- all-or-nothing, at their original quantities. Kefu only: the selection,
-- simulation and execution machinery lives in core/completion_batch.py,
-- core/kefu_turn_apply.py and handlers/uchoice/complete_batch.py, and
-- core/access_control.py hides both services from Smart Robot. Design:
-- docs/reviews/active/2026-09-batch-completion-confirmation/plan.md.
--
-- targets_existing_request = FALSE on purpose: that flag's machinery
-- resolves ONE reference_serial into session.request_log_id. A batch case
-- owns its own placeholder request_log instead (the batch's audit record)
-- and references its targets through collected_fields.reference_serials.
--
-- The AI never supplies reference_serials -- it only proposes a
-- `selection`; code resolves membership (hence required: []).
--
-- Grants mirror the matching single-completion service exactly, for every
-- role that holds it, and group_service rows are added wherever the single
-- service is enabled. Idempotent, following V23's idiom.
-- ============================================================

INSERT INTO service_type (
    service_type_id, name, description, input_schema, group_config_schema,
    confirmation_note, is_active, requires_confirmation,
    targets_existing_request, awaits_completion, keywords
) VALUES (
    'c1000000-0000-0000-0000-000000000012',
    'confirm_inbound_completion_batch',
    '仓库人员一次确认多笔待处理入库申请的实际收货（按原申请数量，全部成功或全部不执行）。用于"全部确认入库""1和3都确认入库""确认 REQ-A 和 REQ-B 入库"这类一次选择两笔及以上的说法；只选一笔时用 confirm_inbound_completion。',
    '{"required": [], "optional": ["selection"], "field_hints": {"selection": "Which pending inbound requests to confirm, as an object: {\"select_all\": true} for 全部/所有; {\"indices\": [1,3]} for positions in the numbered list the user was just shown; {\"serials\": [\"REQ-...\"]} for explicit serial numbers (a unique suffix like \"086\" is fine); {\"exclude_indices\": [2]} / {\"exclude_serials\": [...]} for 除了…. Never invent serials; never restate quantities -- a batch always uses original quantities."}}'::jsonb,
    '{}'::jsonb,
    NULL,
    true, true, false, false,
    '["批量确认入库", "全部确认入库", "一起确认入库"]'::jsonb
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
    'c1000000-0000-0000-0000-000000000013',
    'confirm_outbound_completion_batch',
    '仓库人员一次确认多笔待处理出库申请已实际发货（按原申请数量，全部成功或全部不执行）。用于"全部确认出库""1和3都确认出库""确认 REQ-A 和 REQ-B 出库"这类一次选择两笔及以上的说法；只选一笔时用 confirm_outbound_completion。',
    '{"required": [], "optional": ["selection"], "field_hints": {"selection": "Which pending outbound requests to confirm, as an object: {\"select_all\": true} for 全部/所有; {\"indices\": [1,3]} for positions in the numbered list the user was just shown; {\"serials\": [\"REQ-...\"]} for explicit serial numbers (a unique suffix like \"086\" is fine); {\"exclude_indices\": [2]} / {\"exclude_serials\": [...]} for 除了…. Never invent serials; never restate quantities -- a batch always uses original quantities."}}'::jsonb,
    '{}'::jsonb,
    NULL,
    true, true, false, false,
    '["批量确认出库", "全部确认出库", "一起确认出库"]'::jsonb
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
    'c2000000-0000-0000-0000-000000000012',
    'confirm_inbound_completion_batch',
    'Lock every target, lock the storage-scope union, apply each inbound completion at original quantities (all-or-nothing, savepoint), reply'
)
ON CONFLICT (workflow_id) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO workflow (workflow_id, name, description)
VALUES (
    'c2000000-0000-0000-0000-000000000013',
    'confirm_outbound_completion_batch',
    'Lock every target, lock the storage-scope union, re-simulate and compare picks, apply each outbound completion (all-or-nothing, savepoint), reply'
)
ON CONFLICT (workflow_id) DO UPDATE SET description = EXCLUDED.description;

DELETE FROM workflow_step WHERE workflow_id = 'c2000000-0000-0000-0000-000000000012';
INSERT INTO workflow_step (workflow_id, step_order, step_type, config) VALUES
    ('c2000000-0000-0000-0000-000000000012', 1, 'run_completion_batch', '{"direction": "inbound"}'::jsonb),
    ('c2000000-0000-0000-0000-000000000012', 2, 'reply_wechat', '{}'::jsonb);

DELETE FROM workflow_step WHERE workflow_id = 'c2000000-0000-0000-0000-000000000013';
INSERT INTO workflow_step (workflow_id, step_order, step_type, config) VALUES
    ('c2000000-0000-0000-0000-000000000013', 1, 'run_completion_batch', '{"direction": "outbound"}'::jsonb),
    ('c2000000-0000-0000-0000-000000000013', 2, 'reply_wechat', '{}'::jsonb);

-- Enabled wherever the matching single-completion service is enabled.
INSERT INTO group_service (group_id, service_type_id, workflow_id, config)
SELECT gs.group_id, batch.service_type_id, wf.workflow_id, '{}'::jsonb
FROM (VALUES
    ('confirm_inbound_completion', 'confirm_inbound_completion_batch'),
    ('confirm_outbound_completion', 'confirm_outbound_completion_batch')
) AS pair(single_name, batch_name)
JOIN service_type single ON single.name = pair.single_name
JOIN service_type batch ON batch.name = pair.batch_name
JOIN workflow wf ON wf.name = pair.batch_name
JOIN group_service gs ON gs.service_type_id = single.service_type_id
ON CONFLICT (group_id, service_type_id) DO UPDATE
SET workflow_id = EXCLUDED.workflow_id,
    config = EXCLUDED.config;

-- Same roles as the single-completion service (global grants, see V30).
-- DO NOTHING so a re-run never re-adds a grant an admin revoked afterward.
INSERT INTO role_service_permission (role_id, service_type_id, created_by)
SELECT rsp.role_id, batch.service_type_id, 'migration_v33'
FROM (VALUES
    ('confirm_inbound_completion', 'confirm_inbound_completion_batch'),
    ('confirm_outbound_completion', 'confirm_outbound_completion_batch')
) AS pair(single_name, batch_name)
JOIN service_type single ON single.name = pair.single_name
JOIN service_type batch ON batch.name = pair.batch_name
JOIN role_service_permission rsp ON rsp.service_type_id = single.service_type_id
ON CONFLICT (role_id, service_type_id) DO NOTHING;
