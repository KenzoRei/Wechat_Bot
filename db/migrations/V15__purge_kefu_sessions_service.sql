-- V15: purge_kefu_sessions service_type, for admin discoverability only.
--
-- Actual execution is a fully deterministic, pre-AI exact-phrase command
-- (core/kefu_admin_purge.py) -- it never dispatches through this row's
-- workflow. This row exists solely so "有什么管理员服务"/"怎么清空会话"
-- surfaces the exact trigger phrase via the existing check_services/
-- explain_service machinery, both of which read from group_service/
-- group_service_role like every other service. The workflow deliberately
-- has zero workflow_step rows (valid -- no NOT NULL constraint requires
-- at least one) since nothing ever executes it.
--
-- Idempotent for both an existing deployment and a fresh database.

INSERT INTO service_type (
    name,
    description,
    input_schema,
    requires_confirmation,
    targets_existing_request,
    awaits_completion,
    keywords,
    is_active
)
VALUES (
    'purge_kefu_sessions',
    '管理员专用操作：清空所有进行中（尚未确认或正在收集信息）的企业微信客服会话及其未完成的申请记录，不影响历史记录、库存、地址簿或客户资料。此操作不通过对话理解触发，必须精确发送固定指令：先发送"清空所有进行中会话"查看将要关闭的数量，再发送"确认清空所有进行中会话"执行。',
    '{"required": [], "optional": []}'::jsonb,
    true,
    false,
    false,
    '["清空会话", "清空所有进行中会话", "重置会话", "管理员重置"]'::jsonb,
    true
)
ON CONFLICT (name) DO UPDATE
SET description = EXCLUDED.description,
    input_schema = EXCLUDED.input_schema,
    requires_confirmation = EXCLUDED.requires_confirmation,
    targets_existing_request = EXCLUDED.targets_existing_request,
    awaits_completion = EXCLUDED.awaits_completion,
    keywords = EXCLUDED.keywords,
    is_active = true;

INSERT INTO workflow (name, description)
VALUES (
    'purge_kefu_sessions',
    'Discoverability-only placeholder -- no steps. Real execution is core/kefu_admin_purge.py''s deterministic pre-AI command, never dispatched through this workflow.'
)
ON CONFLICT (name) DO UPDATE
SET description = EXCLUDED.description;

INSERT INTO group_service (group_id, service_type_id, workflow_id, config)
SELECT
    group_config.group_id,
    service_type.service_type_id,
    workflow.workflow_id,
    '{}'::jsonb
FROM group_config, service_type, workflow
WHERE group_config.wechat_group_id = 'wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A'
  AND service_type.name = 'purge_kefu_sessions'
  AND workflow.name = 'purge_kefu_sessions'
ON CONFLICT (group_id, service_type_id) DO UPDATE
SET workflow_id = EXCLUDED.workflow_id,
    config = EXCLUDED.config;

INSERT INTO group_service_role (group_id, service_type_id, role_id, created_by)
SELECT
    group_config.group_id,
    service_type.service_type_id,
    role.role_id,
    'migration_v15'
FROM group_config, service_type, role
WHERE group_config.wechat_group_id = 'wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A'
  AND service_type.name = 'purge_kefu_sessions'
  AND role.name = 'admin'
ON CONFLICT (group_id, service_type_id, role_id) DO NOTHING;
