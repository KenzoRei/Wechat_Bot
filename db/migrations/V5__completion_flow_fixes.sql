\encoding UTF8
-- ============================================================
-- V5: Completion Flow Fixes
-- Logistics WeChat Bot Platform
-- Date: 2026-08-05
--
-- Two content-only fixes found while reviewing confirm_inbound_completion/
-- confirm_outbound_completion's confirm/response messages together:
--
-- 1. reference_serial was optional (input_schema.required = []), which is
--    exactly why a warehouseman's confirmation could show a completely
--    blank summary (nothing required means all_fields_collected could fire
--    with an empty collected_fields). Made required — the AI can still
--    auto-fill it from a single pending candidate without the user literally
--    typing it (that still counts as "collected"), this just guarantees
--    it's actually resolved by confirmation time, not skipped entirely.
--
-- 2. Every confirmation_note in the catalog was written in English while
--    everything else the bot says is Chinese — it renders verbatim in the
--    confirmation footer, so a Chinese-speaking user would see one English
--    sentence dropped into an otherwise-Chinese message. Translated all 9.
-- ============================================================

UPDATE service_type
SET input_schema = '{
    "required": ["reference_serial"],
    "optional": ["received_lines"],
    "field_hints": {
        "received_lines": "What was physically received. Defaults to the original request''s sku_lines for palletized lines if unstated. Loose-type lines always require explicit restatement — there is no sensible default for what a warehouseman physically received.",
        "reference_serial": "The pending inbound request being completed. If omitted, fuzzy-match against the injected candidate list of this warehouseman''s own pending inbound requests. 0 candidates: tell the user nothing is pending. 1: proceed. Multiple: list them and ask which one."
    }
}'
WHERE name = 'confirm_inbound_completion';

UPDATE service_type
SET input_schema = '{
    "required": ["reference_serial"],
    "optional": ["fulfillment_lines"],
    "field_hints": {
        "reference_serial": "The pending outbound request being completed. If omitted, fuzzy-match against the injected candidate list of this warehouseman''s own pending outbound requests, same 0/1/N handling as inbound completion.",
        "fulfillment_lines": "What was physically shipped. Palletized lines can default to \"shipped as requested\". Loose-type lines require explicit source_boxes_per_pallet and resulting_boxes_per_pallet — never defaulted (e.g. picked 1 box off an 80-box pallet, 77 remain: convert_out(sku,80,-1) + convert_in(sku,77,+1))."
    }
}'
WHERE name = 'confirm_outbound_completion';


-- ── confirmation_note translations (English -> Chinese) ─────────────────────

UPDATE service_type SET confirmation_note = '如需完整盘点请使用库存盘点服务，如需内部仓位调拨请使用库存调拨服务。'
WHERE name = 'adjust_storage';

UPDATE service_type SET confirmation_note = '实收数量如与原申请不同，将按您填写的实际数量入账，不会被拦截——请如实填写。'
WHERE name = 'confirm_inbound_completion';

UPDATE service_type SET confirmation_note = '散箱调整前后数量若与原申请箱数不完全对应，系统仅作提示，不会拦截提交。'
WHERE name = 'confirm_outbound_completion';

UPDATE service_type SET confirmation_note = '标签将自动生成并创建OMS工单，运费将计入公司账户。如需变更请立即联系管理员。'
WHERE name = 'fedex_label';

UPDATE service_type SET confirmation_note = '确认信息显示的是与当前库存的差异，而非您填写的原始盘点数——请仔细核对后再确认。'
WHERE name = 'recount_storage';

UPDATE service_type SET confirmation_note = '此步骤暂不会变更库存，仅记录申请——库存将在仓库确认实收后更新。'
WHERE name = 'uchoice_inbound_request';

UPDATE service_type SET confirmation_note = '此步骤暂不会变更库存，仅记录申请——库存将在仓库确认实发后更新。'
WHERE name = 'uchoice_outbound_request';

UPDATE service_type SET confirmation_note = '系统会先尝试匹配已有地址——如匹配成功则更新该地址，否则新建一条。请确认上方显示的是新增还是更新后再提交。'
WHERE name = 'upsert_address';

UPDATE service_type SET confirmation_note = '标签将自动生成，运费将计入公司账户。如需变更请立即联系管理员。'
WHERE name = 'ups_label';
