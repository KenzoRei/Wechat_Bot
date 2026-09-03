\encoding UTF8
-- ============================================================
-- V23: cancel_inbound_request / cancel_outbound_request
-- Logistics WeChat Bot Platform
-- Date: 2026-09-03
--
-- New services letting a confirmed-but-not-yet-completed inbound/outbound
-- request (status='processing') be cancelled by its original creator or an
-- admin -- previously the only paths out of 'processing' were a warehouseman
-- completing it, or (Smart Bot only) the 7-day staleness sweep in
-- jobs/uchoice_daily.py. See
-- docs/archive/collaboration/2026-09-warehouse-array-and-cancel-service/
-- for the full design discussion.
--
-- Idempotent for both an existing deployment and a fresh database, following
-- V15's exact idiom: ON CONFLICT ... DO UPDATE for definitional rows
-- (service_type/workflow/group_service), so a corrected re-run of this
-- migration number during development can't silently leave a
-- half-configured catalog entry in place; ON CONFLICT DO NOTHING for
-- group_service_role, so re-running the migration never silently re-adds a
-- grant an admin deliberately revoked afterward. workflow_step has no
-- unique constraint on (workflow_id, step_order) -- these are brand-new
-- workflows (not a reshuffle of an existing one), so their steps are
-- deleted-then-inserted deterministically instead.
-- ============================================================

INSERT INTO service_type (
    service_type_id, name, description, input_schema, group_config_schema,
    confirmation_note, is_active, requires_confirmation,
    targets_existing_request, awaits_completion, keywords
) VALUES (
    'c1000000-0000-0000-0000-000000000010',
    'cancel_inbound_request',
    '取消一笔已确认、但仓库尚未实际收货确认的入库申请（状态为待处理）。取消后申请作废，不影响库存（入库确认前库存本就未变动）。',
    '{"required": ["reference_serial"], "optional": [], "field_hints": {"reference_serial": "The processing inbound request being cancelled. If omitted, fuzzy-match against the injected candidate list of requests this caller may cancel (their own, or -- for an admin -- any in this group), same 0/1/N handling as completion confirmation."}}'::jsonb,
    '{}'::jsonb,
    '取消后无法恢复，如需重新入库请重新提交申请。',
    true, true, true, false,
    '["取消入库申请", "作废入库", "撤销入库申请"]'::jsonb
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
    'c1000000-0000-0000-0000-000000000011',
    'cancel_outbound_request',
    '取消一笔已确认、但仓库尚未实际发货确认的出库申请（状态为待处理）。取消后申请作废，不影响库存（出库确认前库存本就未变动）。',
    '{"required": ["reference_serial"], "optional": [], "field_hints": {"reference_serial": "The processing outbound request being cancelled. If omitted, fuzzy-match against the injected candidate list of requests this caller may cancel (their own, or -- for an admin -- any in this group), same 0/1/N handling as completion confirmation."}}'::jsonb,
    '{}'::jsonb,
    '取消后无法恢复，如需重新出库请重新提交申请。',
    true, true, true, false,
    '["取消出库申请", "作废出库", "撤销出库申请"]'::jsonb
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
    'c2000000-0000-0000-0000-000000000010',
    'cancel_inbound_request',
    'Validate target request and caller authorization, cancel it, notify (best-effort), reply'
)
ON CONFLICT (workflow_id) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO workflow (workflow_id, name, description)
VALUES (
    'c2000000-0000-0000-0000-000000000011',
    'cancel_outbound_request',
    'Validate target request and caller authorization, cancel it, notify (best-effort), reply'
)
ON CONFLICT (workflow_id) DO UPDATE SET description = EXCLUDED.description;

DELETE FROM workflow_step WHERE workflow_id = 'c2000000-0000-0000-0000-000000000010';
INSERT INTO workflow_step (workflow_id, step_order, step_type, config) VALUES
    ('c2000000-0000-0000-0000-000000000010', 1, 'lookup_and_validate_cancellation', '{"direction": "inbound"}'::jsonb),
    ('c2000000-0000-0000-0000-000000000010', 2, 'cancel_existing_request', '{}'::jsonb),
    ('c2000000-0000-0000-0000-000000000010', 3, 'notify_cancelled_request', '{}'::jsonb),
    ('c2000000-0000-0000-0000-000000000010', 4, 'reply_wechat', '{}'::jsonb);

DELETE FROM workflow_step WHERE workflow_id = 'c2000000-0000-0000-0000-000000000011';
INSERT INTO workflow_step (workflow_id, step_order, step_type, config) VALUES
    ('c2000000-0000-0000-0000-000000000011', 1, 'lookup_and_validate_cancellation', '{"direction": "outbound"}'::jsonb),
    ('c2000000-0000-0000-0000-000000000011', 2, 'cancel_existing_request', '{}'::jsonb),
    ('c2000000-0000-0000-0000-000000000011', 3, 'notify_cancelled_request', '{}'::jsonb),
    ('c2000000-0000-0000-0000-000000000011', 4, 'reply_wechat', '{}'::jsonb);

INSERT INTO group_service (group_id, service_type_id, workflow_id, config)
SELECT gc.group_id, st.service_type_id, wf.workflow_id, '{}'::jsonb
FROM group_config gc, service_type st, workflow wf
WHERE gc.wechat_group_id = 'wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A'
  AND st.name = 'cancel_inbound_request'
  AND wf.name = 'cancel_inbound_request'
ON CONFLICT (group_id, service_type_id) DO UPDATE
SET workflow_id = EXCLUDED.workflow_id,
    config = EXCLUDED.config;

INSERT INTO group_service (group_id, service_type_id, workflow_id, config)
SELECT gc.group_id, st.service_type_id, wf.workflow_id, '{}'::jsonb
FROM group_config gc, service_type st, workflow wf
WHERE gc.wechat_group_id = 'wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A'
  AND st.name = 'cancel_outbound_request'
  AND wf.name = 'cancel_outbound_request'
ON CONFLICT (group_id, service_type_id) DO UPDATE
SET workflow_id = EXCLUDED.workflow_id,
    config = EXCLUDED.config;

-- Grants: customer and admin only. Warehousemen are deliberately excluded --
-- cancellation is scoped by ownership/admin plus group, not by warehouse,
-- and a warehouseman completing a request is the intended alternative to
-- cancelling it.
INSERT INTO group_service_role (group_id, service_type_id, role_id, created_by)
SELECT gc.group_id, st.service_type_id, r.role_id, 'migration_v23'
FROM (VALUES
    ('customer', 'cancel_inbound_request'),
    ('customer', 'cancel_outbound_request'),
    ('admin', 'cancel_inbound_request'),
    ('admin', 'cancel_outbound_request')
) AS grants(role_name, service_name)
JOIN role r ON r.name = grants.role_name
JOIN service_type st ON st.name = grants.service_name
CROSS JOIN (SELECT group_id FROM group_config WHERE wechat_group_id = 'wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A') gc
ON CONFLICT (group_id, service_type_id, role_id) DO NOTHING;
