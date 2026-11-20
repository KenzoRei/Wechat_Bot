\encoding UTF8
-- ============================================================
-- V45: update_address service (edit one existing address)
-- Logistics WeChat Bot Platform
--
-- Step 3 of the add_address / update_address split (v1.12.0), after V43
-- (rename) and V44 (add_address create-only wording). Creates the service,
-- its workflow (update_address -> reply_wechat) and enables it for every
-- group that already has add_address.
--
-- NO ROLE GRANTS are created: access to update_address needs BOTH a
-- role_service_permission row and a group_service row (core/access_control),
-- and the operator assigns roles deliberately (admin panel / role grants).
-- Until then nobody can use it -- default deny.
--
-- Group enablement (group_service) is added ONLY when this run creates the
-- service, so a rerun never re-enables a group an operator disabled.
-- Idempotent. Contains no COMMIT. Apply only inside the paused cutover
-- together with V43/V44 and the v1.12.0 code (CHANGELOG).
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM service_type WHERE name = 'add_address') THEN
        RAISE EXCEPTION 'V45: add_address service_type not found (apply V43/V44 first)';
    END IF;
END $$;

CREATE TEMP TABLE v45_new_service ON COMMIT DROP AS
SELECT 1 AS created
WHERE NOT EXISTS (SELECT 1 FROM service_type WHERE name = 'update_address');

INSERT INTO service_type (
    name, description, input_schema, group_config_schema,
    confirmation_note, is_active, requires_confirmation,
    targets_existing_request, awaits_completion, keywords
) VALUES (
    'update_address',
    '修改地址簿中已有的一条收货地址（公司名、地址、联系人、电话、备注；计费类型和所属仓库仅管理员可改）。新增地址请使用「新增地址」。',
    '{"required": ["matched_address_id"],
      "optional": ["company_name", "addr", "contact", "phone", "note", "charge_type", "warehouse_code"],
      "field_hints": {
        "matched_address_id": "address_id of the address being edited, chosen from the address_book candidate list. Never invent one; if several could match, ask the user which.",
        "company_name": "Only if the user asks to change it. Empty string clears it.",
        "addr": "Only if the user asks to change it. Cannot be cleared.",
        "contact": "Receiver contact person. Only if the user asks to change it. Empty string clears it.",
        "phone": "Receiver contact phone. Only if the user asks to change it. Empty string clears it.",
        "note": "Free text. Only if the user asks to change it. Empty string clears it.",
        "charge_type": "One of short_delivery, delivery, truck_transfer, self_pickup. Admin only; only if the user explicitly asks.",
        "warehouse_code": "Admin only; only if the user explicitly asks."}}'::jsonb,
    '{}'::jsonb,
    '请核对下方「修改内容」的旧值与新值。如这条地址在您确认前已被他人修改，系统会要求您重新发起。',
    true, true, false, false,
    '["更新地址", "修改地址", "编辑地址", "改地址"]'::jsonb
)
ON CONFLICT (name) DO UPDATE SET
    description = EXCLUDED.description,
    input_schema = EXCLUDED.input_schema,
    confirmation_note = EXCLUDED.confirmation_note,
    keywords = EXCLUDED.keywords;

INSERT INTO workflow (name, description)
VALUES ('update_address', 'Update one existing U-Choice address, reply')
ON CONFLICT (name) DO NOTHING;

INSERT INTO workflow_step (workflow_id, step_order, step_type, config)
SELECT w.workflow_id, s.step_order, s.step_type, '{}'::jsonb
FROM workflow w
CROSS JOIN (VALUES (1, 'update_address'), (2, 'reply_wechat')) AS s(step_order, step_type)
WHERE w.name = 'update_address'
  AND NOT EXISTS (
      SELECT 1 FROM workflow_step ws
      WHERE ws.workflow_id = w.workflow_id AND ws.step_order = s.step_order
  );

-- Enable for the groups that already have add_address (first creation only).
INSERT INTO group_service (group_id, service_type_id, workflow_id, config)
SELECT gs.group_id, upd.service_type_id, wf.workflow_id, gs.config
FROM group_service gs
JOIN service_type add_st ON add_st.service_type_id = gs.service_type_id AND add_st.name = 'add_address'
JOIN service_type upd ON upd.name = 'update_address'
JOIN workflow wf ON wf.name = 'update_address'
WHERE EXISTS (SELECT 1 FROM v45_new_service)
  AND NOT EXISTS (
      SELECT 1 FROM group_service x
      WHERE x.group_id = gs.group_id AND x.service_type_id = upd.service_type_id
  );

DO $$
BEGIN
    IF (SELECT count(*) FROM service_type WHERE name = 'update_address') <> 1
       OR (SELECT count(*) FROM workflow WHERE name = 'update_address') <> 1
       OR (SELECT count(*) FROM workflow_step ws JOIN workflow w ON w.workflow_id = ws.workflow_id
           WHERE w.name = 'update_address') <> 2 THEN
        RAISE EXCEPTION 'V45: update_address catalog incomplete after insert';
    END IF;
END $$;
