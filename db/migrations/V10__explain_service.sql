-- ── explain_service ──────────────────────────────────────────────────────
-- New service: any registered member can ask "what does X do / how do I use
-- it" for a specific service, and get back the real, admin-written
-- description verbatim — the AI only identifies WHICH service is being
-- asked about, it never authors the explanation itself (avoids the AI
-- paraphrasing/potentially getting business rules subtly wrong).
--
-- descriptions were English-only, internal-classification-facing text.
-- Since they're now also shown directly to customers via explain_service,
-- they're rewritten here in Chinese with more detail. keywords is new:
-- explicit trigger phrases surfaced to the AI on every turn (not just for
-- explain_service) to reinforce normal request routing too — advisory only,
-- see the corresponding prompt rule in ai/prompt_builder.py that keywords
-- must never override an explicit user correction.

ALTER TABLE service_type ADD COLUMN keywords JSONB NOT NULL DEFAULT '[]'::jsonb;

UPDATE service_type SET
    description = '客户提交入库申请，告知仓库将有商品送到仓库，说明商品种类、数量以及是否需要拆包。提交后进入待处理状态，库存不会立即变动，需仓库人员实际收货并确认后才更新。',
    keywords = '["入库", "收货", "上架", "入仓", "到货登记"]'
WHERE name = 'uchoice_inbound_request';

UPDATE service_type SET
    description = '仓库人员确认已实际收到某笔入库申请的货物，登记实收数量（如与申请不同以实收为准），确认后库存正式增加。',
    keywords = '["确认入库", "收到", "入库确认", "已收货", "签收"]'
WHERE name = 'confirm_inbound_completion';

UPDATE service_type SET
    description = '客户提交出库申请，说明要运出的商品、数量及目的地（可以是外部地址，也可以是我方另一仓库的调仓）。提交后进入待处理状态，库存不会立即变动，需仓库人员实际发货并确认后才更新。',
    keywords = '["出库", "送货", "发货", "提货", "调仓"]'
WHERE name = 'uchoice_outbound_request';

UPDATE service_type SET
    description = '仓库人员确认已实际发出某笔出库申请的货物，登记实发数量（如与申请不同以实发为准）。如目的地是我方另一仓库，确认后会同时增加该仓库库存（内部调仓）。',
    keywords = '["确认出库", "送到", "出库确认", "已发货"]'
WHERE name = 'confirm_outbound_completion';

UPDATE service_type SET
    description = '仓库人员对某个仓库的库存做一次性手动修正，用于登记货损、丢失或盘点差异，直接修改库存数量。',
    keywords = '["库存调整", "货损", "丢货", "库存修正"]'
WHERE name = 'adjust_storage';

UPDATE service_type SET
    description = '仓库人员上报某个仓库的完整实盘库存快照，系统自动计算与当前记录的差异并批量调整。',
    keywords = '["盘点", "整仓盘点", "库存清点"]'
WHERE name = 'recount_storage';

UPDATE service_type SET
    description = '仓库人员在同一仓库内，将箱子从一个托盘规格调整到另一个（重新打托/合并零散库存），不改变商品总数。',
    keywords = '["移库", "拆托", "并托", "重新打托"]'
WHERE name = 'move_storage';

UPDATE service_type SET
    description = '管理员修改群内成员的角色，如设为仓库管理员还需指定负责的仓库。',
    keywords = '["修改角色", "设置权限", "变更角色"]'
WHERE name = 'role_change';

UPDATE service_type SET
    description = '新增或更新共享地址簿中的收货地址，含公司名称、详细地址及对应计费类型。',
    keywords = '["新增地址", "地址簿", "维护地址"]'
WHERE name = 'upsert_address';

UPDATE service_type SET
    description = '查询仓库当前库存余额，可按仓库和/或商品筛选，立即返回结果。',
    keywords = '["查库存", "库存查询", "现有库存"]'
WHERE name = 'view_storage';

UPDATE service_type SET
    description = '查询某仓库在指定月份（或范围）内的库存变动明细，立即返回结果。',
    keywords = '["库存记录", "出入库记录", "库存历史"]'
WHERE name = 'view_storage_history';

UPDATE service_type SET
    description = '查询某仓库在指定月份（或范围）内的运营费用汇总（运输/打托/拆包/仓储费）——这是仓库整体运营成本报告，不是按客户单独出具的账单。',
    keywords = '["账单", "费用报告", "发票"]'
WHERE name = 'view_invoice';

UPDATE service_type SET
    description = '通过易递达生成 FedEx 快递标签，可选择关联 OMS 出库订单。',
    keywords = '["fedex", "联邦快递", "快递标签"]'
WHERE name = 'fedex_label';

UPDATE service_type SET
    description = '通过易递达生成 UPS 快递标签。',
    keywords = '["ups", "快递标签"]'
WHERE name = 'ups_label';

INSERT INTO service_type (
    service_type_id, name, description, input_schema, group_config_schema,
    confirmation_note, requires_confirmation, targets_existing_request, keywords
) VALUES (
    'c1000000-0000-0000-0000-00000000000d',
    'explain_service',
    '介绍某项具体服务的用途、使用场景和触发方式。',
    '{
        "required": ["target_service_name"],
        "optional": [],
        "field_hints": {
            "target_service_name": "The internal service name being asked about — resolve by matching the user''s question against every service''s name/description/keywords already present in context, regardless of whether the caller can actually use that service themselves."
        }
    }',
    '{}',
    NULL,
    FALSE, FALSE,
    '["是什么", "怎么用", "介绍一下", "详细说明"]'
);

INSERT INTO workflow (workflow_id, name, description) VALUES
    ('c2000000-0000-0000-0000-00000000000d', 'explain_service',
     'Look up a service''s stored description/keywords and reply verbatim, immediately');

INSERT INTO workflow_step (workflow_id, step_order, step_type, config) VALUES
    ('c2000000-0000-0000-0000-00000000000d', 1, 'explain_service', '{}'),
    ('c2000000-0000-0000-0000-00000000000d', 2, 'reply_wechat',    '{}');
