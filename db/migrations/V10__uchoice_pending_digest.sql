\encoding UTF8
-- ============================================================
-- V10: on-demand pending-request digest service
-- Logistics WeChat Bot Platform
-- Date: 2026-08-11
--
-- kefu-migration-plan.md Sec 7: "pull, not push" -- the scheduled
-- jobs/uchoice_daily.py digest stays as-is (now channel-filtered, see
-- app-level changes), but callers should also be able to ask for the same
-- content on demand rather than waiting for the next 08:00 push. New
-- read-only service_type (requires_confirmation=false, executes
-- immediately), following the exact same shape as view_storage/
-- view_storage_history. No group grant seeded here -- same practice as
-- every prior migration, granted per-group via the admin API once a
-- group actually wants it.
-- ============================================================

INSERT INTO service_type (service_type_id, name, description, input_schema, group_config_schema, confirmation_note, requires_confirmation, targets_existing_request, keywords) VALUES
(
    'c1000000-0000-0000-0000-00000000000e',
    'view_pending_digest',
    '查询当前所有仍在处理中（未完成）的入库/出库申请，按提交时间排序，立即返回结果。',
    '{"optional": [], "required": [], "field_hints": {}}',
    '{}',
    NULL,
    FALSE,
    FALSE,
    '["待处理", "还没完成", "进度", "待办"]'
);

INSERT INTO workflow (workflow_id, name, description) VALUES
('c2000000-0000-0000-0000-00000000000e', 'view_pending_digest', 'Query all pending inbound/outbound requests for this group, reply immediately');

INSERT INTO workflow_step (workflow_id, step_order, step_type, config) VALUES
('c2000000-0000-0000-0000-00000000000e', 1, 'query_pending_digest', '{}'),
('c2000000-0000-0000-0000-00000000000e', 2, 'reply_wechat', '{}');
