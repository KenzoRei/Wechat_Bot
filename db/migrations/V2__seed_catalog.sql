\encoding UTF8
-- ============================================================
-- V2: Seed Catalog + Real Address Book + Group Onboarding
-- (consolidated baseline, squashed 2026-08-05)
-- Logistics WeChat Bot Platform
--
-- Dumped directly from the live database via pg_dump --data-only, so this
-- reflects exactly what was actually seeded (roles, service types,
-- workflows, SKU catalog, the 27-entry real address book from
-- Outbound_Sample.xlsx via V13, plus the real WeChat group's onboarding --
-- group_config, group_member, group_service, group_service_role) rather
-- than a hand-transcribed approximation. role_id/group_id/etc. are
-- explicit UUID values matching what was already live, so this file is a
-- faithful re-seed, not a fresh-random-UUID reset. One row was dropped:
-- a "TEST Logistics" uchoice_address entry created during earlier chat
-- testing, not a real business address.
-- ============================================================

-- ── Catalog: roles, service types, workflows, SKUs, addresses ──────────────

--
-- PostgreSQL database dump
--


-- Dumped from database version 18.4 (Debian 18.4-1.pgdg12+1)
-- Dumped by pg_dump version 18.3

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Data for Name: role; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.role VALUES ('69c72ff1-410b-4f83-a81a-b8687424adf7', 'admin', 'Full access to group services and admin-level actions', '2026-08-04 17:39:23.271477+00');
INSERT INTO public.role VALUES ('3e8e58c5-38a3-4dd2-8972-1a5a2a979d0b', 'customer', 'Standard requester — access limited to explicitly granted services', '2026-08-04 17:39:23.271477+00');
INSERT INTO public.role VALUES ('e640c95d-8e1f-429e-8436-c93a79dc585a', 'warehouseman', 'Confirms inbound/outbound completions, corrects storage (adjust/recount/move)', '2026-08-04 17:39:29.572195+00');
INSERT INTO public.role VALUES ('7544f868-bdc8-4c12-addd-2119b6bf583e', 'accountant', 'Read-only financial visibility — storage and invoice viewing', '2026-08-04 17:39:29.572195+00');


--
-- Data for Name: service_type; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.service_type VALUES ('a1b2c3d4-0002-0000-0000-000000000002', 'ups_label', '通过易递达生成 UPS 快递标签。', '{"optional": ["service_level", "shipper_corp_name", "shipper_country", "recipient_corp_name", "recipient_country", "length_in", "width_in", "height_in", "reference_number"], "required": ["shipper_name", "shipper_phone", "shipper_street", "shipper_city", "shipper_state", "shipper_zip", "recipient_name", "recipient_phone", "recipient_street", "recipient_city", "recipient_state", "recipient_zip", "weight_lbs"], "field_hints": {"weight_lbs": "numeric value in pounds", "service_level": "e.g. UPS_GROUND, UPS_2ND_DAY_AIR, UPS_NEXT_DAY_AIR, default is UPS_GROUND", "shipper_country": "default is US", "reference_number": "Optional field that appears on the label for your reference (e.g. order number, customer name)", "recipient_country": "default is US"}}', '{"optional": ["ydd_account_code"], "required": ["ydd_api_key", "ydd_cust_id", "ydd_channel_id"], "field_hints": {"ydd_api_key": "YiDiDa API key for this customer group", "ydd_cust_id": "YiDiDa customer ID, provided during YiDiDa onboarding", "ydd_channel_id": "YiDiDa channel ID for this shipper account", "ydd_account_code": "Optional billing account code"}}', '标签将自动生成，运费将计入公司账户。如需变更请立即联系管理员。', true, '2026-08-04 17:39:23.301535+00', true, false, false, '["ups", "快递标签"]');
INSERT INTO public.service_type VALUES ('c1000000-0000-0000-0000-000000000004', 'confirm_outbound_completion', '仓库人员确认已实际发出某笔出库申请的货物，登记实发数量（如与申请不同以实发为准）。如目的地是我方另一仓库，确认后会同时增加该仓库库存（内部调仓）。', '{"optional": ["fulfillment_lines"], "required": ["reference_serial"], "field_hints": {"reference_serial": "The pending outbound request being completed. If omitted, fuzzy-match against the injected candidate list of this warehouseman''s own pending outbound requests, same 0/1/N handling as inbound completion.", "fulfillment_lines": "What was physically shipped. Palletized lines can default to \"shipped as requested\". Loose-type lines require explicit source_boxes_per_pallet and resulting_boxes_per_pallet — never defaulted (e.g. picked 1 box off an 80-box pallet, 77 remain: convert_out(sku,80,-1) + convert_in(sku,77,+1))."}}', '{}', '散箱调整前后数量若与原申请箱数不完全对应，系统仅作提示，不会拦截提交。', true, '2026-08-04 17:39:29.626499+00', true, true, false, '["确认出库", "送到", "出库确认", "已发货"]');
INSERT INTO public.service_type VALUES ('c1000000-0000-0000-0000-000000000005', 'view_storage', '查询仓库当前库存余额，可按仓库和/或商品筛选，立即返回结果。', '{"optional": ["warehouse_code", "sku_code"], "required": [], "field_hints": {"sku_code": "Omit for all SKUs.", "warehouse_code": "Omit for both warehouses."}}', '{}', NULL, true, '2026-08-04 17:39:29.626499+00', false, false, false, '["查库存", "库存查询", "现有库存"]');
INSERT INTO public.service_type VALUES ('c1000000-0000-0000-0000-000000000003', 'confirm_inbound_completion', '仓库人员确认已实际收到某笔入库申请的货物，登记实收数量（如与申请不同以实收为准），确认后库存正式增加。', '{"optional": ["received_lines"], "required": ["reference_serial"], "field_hints": {"received_lines": "What was physically received. Defaults to the original request''s sku_lines for palletized lines if unstated. Loose-type lines always require explicit restatement — there is no sensible default for what a warehouseman physically received.", "reference_serial": "The pending inbound request being completed. If omitted, fuzzy-match against the injected candidate list of this warehouseman''s own pending inbound requests. 0 candidates: tell the user nothing is pending. 1: proceed. Multiple: list them and ask which one."}}', '{}', '实收数量如与原申请不同，将按您填写的实际数量入账，不会被拦截——请如实填写。', true, '2026-08-04 17:39:29.626499+00', true, true, false, '["确认入库", "收到", "入库确认", "已收货", "签收"]');
INSERT INTO public.service_type VALUES ('c1000000-0000-0000-0000-000000000006', 'view_storage_history', '查询某仓库在指定月份（或范围）内的库存变动明细，立即返回结果。', '{"optional": [], "required": ["warehouse_code", "start_month", "end_month"], "field_hints": {"end_month": "e.g. 2026-03 — last month of the range (inclusive). Equal to start_month for a single-month query. If it is the current month, results are naturally capped at today since nothing later exists yet.", "start_month": "e.g. 2026-01 — first month of the range (inclusive), month granularity not a free date.", "warehouse_code": "JFK or DE"}}', '{}', NULL, true, '2026-08-04 17:39:29.626499+00', false, false, false, '["库存记录", "出入库记录", "库存历史"]');
INSERT INTO public.service_type VALUES ('c1000000-0000-0000-0000-00000000000c', 'view_invoice', '查询某仓库在指定月份（或范围）内的运营费用汇总（运输/打托/拆包/仓储费）——这是仓库整体运营成本报告，不是按客户单独出具的账单。', '{"optional": [], "required": ["warehouse_code", "start_month", "end_month"], "field_hints": {"end_month": "e.g. 2026-03 — last month of the range (inclusive). Equal to start_month for a single-month invoice.", "start_month": "e.g. 2026-01 — first month of the range (inclusive), month granularity not a free date.", "warehouse_code": "JFK or DE"}}', '{}', NULL, true, '2026-08-04 17:39:29.626499+00', false, false, false, '["账单", "费用报告", "发票"]');
INSERT INTO public.service_type VALUES ('c1000000-0000-0000-0000-000000000007', 'adjust_storage', '仓库人员对某个仓库的库存做一次性手动修正，用于登记货损、丢失或盘点差异，直接修改库存数量。', '{"optional": [], "required": ["warehouse_code", "adjustment_lines"], "field_hints": {"adjustment_lines": "Array of {sku_code, boxes_per_pallet, pallet_delta, reason} — plural, so one correction session can report several adjustments in one message."}}', '{}', '如需完整盘点请使用库存盘点服务，如需内部仓位调拨请使用库存调拨服务。', true, '2026-08-04 17:39:29.626499+00', true, false, false, '["库存调整", "货损", "丢货", "库存修正"]');
INSERT INTO public.service_type VALUES ('c1000000-0000-0000-0000-000000000008', 'recount_storage', '仓库人员上报某个仓库的完整实盘库存快照，系统自动计算与当前记录的差异并批量调整。', '{"optional": [], "required": ["warehouse_code", "inventory_lines"], "field_hints": {"inventory_lines": "Full snapshot, not a delta — array of {sku_code, boxes_per_pallet, pallet_count}. Any existing bucket omitted from this snapshot is treated as now zero, not unchanged."}}', '{}', '确认信息显示的是与当前库存的差异，而非您填写的原始盘点数——请仔细核对后再确认。', true, '2026-08-04 17:39:29.626499+00', true, false, false, '["盘点", "整仓盘点", "库存清点"]');
INSERT INTO public.service_type VALUES ('c1000000-0000-0000-0000-00000000000a', 'upsert_address', '新增或更新共享地址簿中的收货地址，含公司名称、详细地址及对应计费类型。', '{"optional": ["note", "company_name"], "required": ["charge_type", "addr", "warehouse_code"], "field_hints": {"note": "Free text, e.g. a nickname for the address.", "charge_type": "One of short_delivery, delivery, truck_transfer, self_pickup.", "company_name": "Optional — some real addresses are only ever referred to by a bare address or a location nickname, with no formal company name ever given. Leave unset rather than guessing one.", "warehouse_code": "JFK or DE — which warehouse this address is associated with. Required for every address, not just truck_transfer ones."}}', '{}', '系统会先尝试匹配已有地址——如匹配成功则更新该地址，否则新建一条。请确认上方显示的是新增还是更新后再提交。', true, '2026-08-04 17:39:29.626499+00', true, false, false, '["新增地址", "地址簿", "维护地址"]');
INSERT INTO public.service_type VALUES ('c1000000-0000-0000-0000-000000000001', 'uchoice_inbound_request', '客户提交入库申请，告知仓库将有商品送到仓库，说明商品种类、数量以及是否需要拆包。提交后进入待处理状态，库存不会立即变动，需仓库人员实际收货并确认后才更新。', '{"optional": ["needs_unpacking"], "required": ["warehouse_code", "sku_lines"], "field_hints": {"sku_lines": "Array of line items. Each is either palletized {sku_code, boxes_per_pallet, pallet_count} or loose {sku_code, box_count}.", "warehouse_code": "JFK or DE", "needs_unpacking": "Boolean — true if this inbound shipment needs to be unpacked/unpalletized by warehouse staff on arrival ($300 flat fee). Always ask and always show in the confirmation, even if false."}}', '{}', '此步骤暂不会变更库存，仅记录申请——库存将在仓库确认实收后更新。', true, '2026-08-04 17:39:29.626499+00', true, false, true, '["入库", "收货", "上架", "入仓", "到货登记"]');
INSERT INTO public.service_type VALUES ('c1000000-0000-0000-0000-000000000009', 'move_storage', '仓库人员在同一仓库内，将箱子从一个托盘规格调整到另一个（重新打托/合并零散库存），不改变商品总数。', '{"optional": [], "required": ["warehouse_code", "move_lines"], "field_hints": {"move_lines": "Array of {sku_code, source_boxes_per_pallet, box_count_moved, target_boxes_per_pallet}. Nothing enters or leaves the warehouse — the source bucket loses box_count_moved boxes, the target bucket gains them."}}', '{}', NULL, true, '2026-08-04 17:39:29.626499+00', true, false, false, '["移库", "拆托", "并托", "重新打托"]');
INSERT INTO public.service_type VALUES ('c1000000-0000-0000-0000-00000000000b', 'role_change', '管理员修改群内成员的角色，如设为仓库管理员还需指定负责的仓库。', '{"optional": ["warehouse_code"], "required": ["target_openid", "new_role"], "field_hints": {"new_role": "One of admin, customer, warehouseman, accountant.", "target_openid": "Resolve via the injected member-list candidate list (wechat_openid + display_name + current role) against a casual name reference.", "warehouse_code": "Required only if new_role is warehouseman."}}', '{}', NULL, true, '2026-08-04 17:39:29.626499+00', true, false, false, '["修改角色", "设置权限", "变更角色"]');
INSERT INTO public.service_type VALUES ('a1b2c3d4-0001-0000-0000-000000000001', 'fedex_label', '通过易递达生成 FedEx 快递标签，可选择关联 OMS 出库订单。', '{"optional": ["oms_outbound_order_no", "service_level", "shipper_corp_name", "shipper_country", "recipient_corp_name", "recipient_country", "length_in", "width_in", "height_in", "reference_number"], "required": ["shipper_name", "shipper_phone", "shipper_street", "shipper_city", "shipper_state", "shipper_zip", "recipient_name", "recipient_phone", "recipient_street", "recipient_city", "recipient_state", "recipient_zip", "weight_lbs"], "field_hints": {"weight_lbs": "numeric value in pounds", "service_level": "e.g. PRIORITY_OVERNIGHT, STANDARD_OVERNIGHT, FEDEX_GROUND, default is FEDEX_GROUND", "shipper_country": "default is US", "reference_number": "Optional field that appears on the label for your reference (e.g. order number, customer name)", "recipient_country": "default is US", "oms_outbound_order_no": "OMS outbound order number (e.g. OBS0162604110RV) — only collect if the customer volunteers it, never ask proactively. Links the created label to their existing OMS order."}}', '{"optional": ["ydd_account_code"], "required": ["ydd_api_key", "ydd_cust_id", "ydd_channel_id", "oms_app_key", "oms_app_secret", "oms_wh_code"], "field_hints": {"oms_app_key": "OMS App_Key from xlwms admin portal", "oms_wh_code": "OMS warehouse code fallback (e.g. DE19713), used if the outbound order query returns none", "ydd_api_key": "YiDiDa API key for this customer group", "ydd_cust_id": "YiDiDa customer ID, provided during YiDiDa onboarding", "oms_app_secret": "OMS App_Secret from xlwms admin portal", "ydd_channel_id": "YiDiDa channel ID for this shipper account", "ydd_account_code": "Optional billing account code"}}', '标签将自动生成并创建OMS工单，运费将计入公司账户。如需变更请立即联系管理员。', true, '2026-08-04 17:39:23.301535+00', true, false, false, '["fedex", "联邦快递", "快递标签"]');
INSERT INTO public.service_type VALUES ('c1000000-0000-0000-0000-00000000000d', 'explain_service', '介绍某项具体服务的用途、使用场景和触发方式。', '{"optional": [], "required": ["target_service_name"], "field_hints": {"target_service_name": "The internal service name being asked about — resolve by matching the user''s question against every service''s name/description/keywords already present in context, regardless of whether the caller can actually use that service themselves."}}', '{}', NULL, true, '2026-08-05 05:34:38.693376+00', false, false, false, '["是什么", "怎么用", "介绍一下", "详细说明"]');
INSERT INTO public.service_type VALUES ('c1000000-0000-0000-0000-000000000002', 'uchoice_outbound_request', '客户提交出库申请，说明要运出的商品、数量及目的地（可以是外部地址，也可以是我方另一仓库的调仓）。提交后进入待处理状态，库存不会立即变动，需仓库人员实际发货并确认后才更新。', '{"optional": ["new_pallet_count", "warehouse_code"], "required": ["sku_lines", "destination_address_id"], "field_hints": {"sku_lines": "Array of line items. Each is either palletized {sku_code, boxes_per_pallet, pallet_count} or loose {sku_code, box_count}. For a palletized line missing boxes_per_pallet, match against the injected current storage buckets for that SKU+warehouse and propose the largest available bucket as a default — always show this default explicitly in the confirmation so the customer can correct it.", "warehouse_code": "JFK or DE. Real customers almost never state this explicitly — if not mentioned, do NOT ask; leave it unset and the system will default to JFK, shown in the confirmation for the customer to correct.", "new_pallet_count": "$15/pallet — customer wants loose boxes consolidated onto a fresh pallet before shipping. Always ask and always show in the confirmation, even if 0/absent.", "destination_address_id": "Resolve by fuzzy-matching the customer''s description against the injected address candidate list."}}', '{}', '此步骤暂不会变更库存，仅记录申请——库存将在仓库确认实发后更新。', true, '2026-08-04 17:39:29.626499+00', true, false, true, '["出库", "送货", "发货", "提货", "调仓"]');


--
-- Data for Name: uchoice_address; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.uchoice_address VALUES ('8d2f7c73-122a-4d90-8424-03e1081b2b34', 'DE Warehouse', 'truck_transfer', '201 Gabor DR, Newark, DE 19711', 'JFK', 'DE warehouse', 'system', '2026-08-04 17:39:29.606286+00', 'DE');
INSERT INTO public.uchoice_address VALUES ('f7543425-4bca-4a3a-8445-d0a3b8badcb8', 'JFK Warehouse', 'truck_transfer', '14502 156th St, Jamaica, NY 11434', 'DE', 'JFK warehouse', 'system', '2026-08-04 17:39:29.606286+00', 'JFK');
INSERT INTO public.uchoice_address VALUES ('bb8c35cb-dda1-404d-9294-682ed3df5659', 'JFK仓库自提留存', 'self_pickup', '14502 156th St, Jamaica, NY 11434', 'JFK', '仓库自留，货物不离开JFK仓', 'system', '2026-08-05 05:34:46.417347+00', NULL);
INSERT INTO public.uchoice_address VALUES ('a25bb922-2340-41ac-8e2d-f0a5192d85d0', 'DE仓库自提留存', 'self_pickup', '201 Gabor DR, Newark, DE 19711', 'DE', '仓库自留，货物不离开DE仓', 'system', '2026-08-05 05:34:46.417347+00', NULL);
INSERT INTO public.uchoice_address VALUES ('5a1cd32c-f2a3-492d-b2c3-360acfff7514', '散客', 'self_pickup', '', NULL, '客户本人到仓库自行取货，不限仓库', 'system', '2026-08-05 05:34:46.417347+00', NULL);
INSERT INTO public.uchoice_address VALUES ('2d27e83a-7ac5-4efe-b922-f1a3192a0257', 'First Wholesale Inc', 'truck_transfer', '5201 #1 Flushing Ave, Maspeth, NY 11378', 'JFK', NULL, 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('8e946762-4bfb-43bf-8cc4-657bce661122', '新Fast Track (New Fast Track)', 'delivery', '23039 International Airport Center Blvd, Jamaica, NY 11413', 'JFK', '别名/nickname, no formal company name given in messages', 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('c45b4af4-4d66-4d4d-9365-e2f5508a75c0', 'Five Goods Inc', 'delivery', '2-39 54th Ave, Long Island City, NY 11101', 'JFK', NULL, 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('3e31b7c4-c1c5-4014-a56c-c0d709eceae8', 'Savino Del Bene Warehouse', 'truck_transfer', '34 Engelhard Ave, Avenel, NJ 07001', 'JFK', 'Att: Richard / Edwin. Street number given as ''34'' in A4 but ''4'' in A49 -- likely a typo, needs confirming', 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('07b13764-2779-45c8-a338-8f115d4f3dab', 'JEC Logistics New Jersey Inc', 'truck_transfer', '200 Ludlow Dr STE B, Ewing Township, NJ 08638', 'JFK', 'Contact: Lucy +1 929-218-8278. Note: customer pays by check on delivery', 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('ef658481-35d4-48e1-bd66-adab96e50874', 'Happy Toys Wholesale', 'truck_transfer', '47-08 Grand Ave, Queens, NY 11378', 'JFK', 'Contact: 917-855-7957 (老板娘)', 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('9bc1f6bb-18c3-4252-ab6f-069ac538515f', '旧Fast Track (Old Fast Track)', 'delivery', '182-08 149th Avenue, Springfield Gardens, NY 11413', 'JFK', '别名/nickname, no formal company name given in messages', 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('d387d636-9104-4064-98ef-05f69dbb915c', 'Yigo Consulting', 'truck_transfer', '182-30 150th Rd, Jamaica, NY 11413', 'JFK', NULL, 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('4c12b471-96bf-46ed-b7aa-5d842a3004b5', NULL, 'truck_transfer', '114-01 14th Ave, College Point, NY 11356', 'JFK', 'Contact (A11 only): John 917-468-1500. A41/A45 give no company name', 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('5f0c443e-0c72-4f7e-9171-1f64a713ee80', 'CM Distribution NJ Inc', 'truck_transfer', '205 Campus Dr, Kearny, NJ 07032', 'JFK', 'Contact: Noah 929-699-3636', 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('1d5f50ba-d3a9-404e-8a27-2316e8796eed', NULL, 'truck_transfer', '200 Knickerbocker Ave, Bohemia, NY 11716', 'JFK', 'No company name given, closes at 5pm', 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('40a2da62-cc26-4e17-9e69-ac8b1b5a879f', NULL, 'truck_transfer', '330A Casanova St, Bronx, NY 10474', 'JFK', 'Phone: 917-847-2058', 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('bfa4eb0b-f64a-4894-8f3f-885073705c96', NULL, 'truck_transfer', '1309 E Bay Ave 1309B, Bronx, NY 10474', 'JFK', 'Recipient: Vivek, phone 516-305-9127', 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('1cb80ea4-d835-4291-97b1-a452d9c31d95', 'CBL Trading Inc', 'truck_transfer', '316 Meserole St, Brooklyn, NY 11206', 'JFK', 'Contact: Bryan 516-304-9721', 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('8c3449eb-9760-48de-bbf0-ecb2cecf0a4b', 'NY 99 Supplier', 'truck_transfer', '145 Gardner Ave, Brooklyn, NY', 'JFK', 'Phone: 718-381-3368. No zip given in message', 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('9173b0c0-8b57-4264-ac7e-131cc15d01e0', NULL, 'truck_transfer', '77 Metro Wy STE2, Secaucus, NJ 07094', 'JFK', 'Phone: 718-715-8729', 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('1e6671d1-638c-44ed-beec-50a6493a4f2c', 'Speeder Solution', 'truck_transfer', '333 Centerpoint Boulevard, New Castle, DE 19720', 'JFK', NULL, 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('709fa783-3ef4-4cf3-92f1-477f8de06f54', 'Speeder Solution', 'delivery', '333 Centerpoint Boulevard, New Castle, DE 19720', 'DE', NULL, 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('a1bf1107-4da2-40d5-88a2-6c9d5d687efb', 'Good Fortune', 'delivery', '5851 Maspeth Ave, Maspeth, NY 11378', 'JFK', 'Phone: 718-288-3599', 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('2730cf76-94e7-4565-bb2d-1dbad69fd122', 'Skysunco Wholesale Inc', 'truck_transfer', '220 Ingraham St, Brooklyn, NY 11237', 'JFK', 'Contact: 吴老板 (Mr. Wu) 917-582-8818', 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('bc2510e0-b076-45ae-ade0-8571de74d2b8', '旧Longo (Old Longo)', 'delivery', '2179-20 149th Ave, Jamaica, NY 11434', 'JFK', '别名/nickname, no formal company name given in message', 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('5c037aae-36f0-4605-b4e9-3ece806ff6df', 'GYC Logistics Inc', 'delivery', '970 New Brunswick Ave Unit 4, Rahway, NJ 07065', 'JFK', 'Contact: Reggie 551-777-1437', 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('45f4face-2641-4e91-a75a-a7ec735102e8', '高泰批发 (Gaotai Wholesale)', 'truck_transfer', '52-01 Flushing Avenue #8, Maspeth, NY 11378', 'JFK', 'Phone: 917-662-9828. ', 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('ee26fbc2-df4b-4eff-aa5e-ff412e7b69fb', 'Tolead Logistics JFK Inc', 'delivery', '107 Charles Lindbergh Blvd, Garden City, NY 11530', 'JFK', 'Contact: Peter +1 929-319-9913', 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('06086865-3ca2-4d4a-8726-6914ad118ec3', 'NJ01-Anmei Group Warehouse #31 Dock', 'truck_transfer', '1515 Burnt Mill Rd Dock 31, Cherry Hill, NJ 08003-3637', 'JFK', 'Phone: 646-269-0236 (Allen)', 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('68207234-9e6e-459a-b86a-65621a93e118', 'Wuxing Inc', 'delivery', '1558 127th St, College Point, NY', 'JFK', 'Phone: 917-660-7272. Driver nav hint: also findable as ''126-07 18th Ave'' (back door)', 'system', '2026-08-05 22:06:20.61259+00', NULL);
INSERT INTO public.uchoice_address VALUES ('695415f1-a892-45db-ab3d-2fcf4a06be90', 'Prestige JFK Inc', 'delivery', '147-06 176th St, Jamaica, NY 11434', 'JFK', 'Contact: Peng 917-770-1365. Prefers morning delivery', 'system', '2026-08-05 22:06:20.61259+00', NULL);


--
-- Data for Name: uchoice_sku; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.uchoice_sku VALUES ('s1', 'S1 22 lb Stretch Wrap');
INSERT INTO public.uchoice_sku VALUES ('s2', 'S2 1500 ft Stretch Wrap');
INSERT INTO public.uchoice_sku VALUES ('s3', 'S3 Black Stretch Wrap');
INSERT INTO public.uchoice_sku VALUES ('s4', 'S4 1000 ft Stretch Wrap');
INSERT INTO public.uchoice_sku VALUES ('t1', 'T1 3-inch Clear Packing Tape');
INSERT INTO public.uchoice_sku VALUES ('t2', 'T2 3-inch Dark Brown Packing Tape');
INSERT INTO public.uchoice_sku VALUES ('t3', 'T3 3-inch Light Brown Packing Tape');
INSERT INTO public.uchoice_sku VALUES ('t4', 'T4 2-inch Clear Packing Tape');


--
-- Data for Name: workflow; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.workflow VALUES ('af000001-0000-0000-0000-000000000005', 'fedex_workorder', 'Create FedEx label via YiDiDa, create OMS work order (linked if OMS order no. provided), reply to WeChat', '2026-08-04 17:39:23.315062+00');
INSERT INTO public.workflow VALUES ('af000001-0000-0000-0000-000000000004', 'ups_only', 'Create UPS label via YiDiDa, reply to WeChat — no OMS work order', '2026-08-04 17:39:23.315062+00');
INSERT INTO public.workflow VALUES ('c2000000-0000-0000-0000-000000000001', 'uchoice_inbound_request', 'Record an inbound request, reply — no storage change yet', '2026-08-04 17:39:29.64121+00');
INSERT INTO public.workflow VALUES ('c2000000-0000-0000-0000-000000000002', 'uchoice_outbound_request', 'Record an outbound request, reply — no storage change yet', '2026-08-04 17:39:29.64121+00');
INSERT INTO public.workflow VALUES ('c2000000-0000-0000-0000-000000000003', 'confirm_inbound_completion', 'Validate target request, apply inbound storage txn, stub receiving PDF, complete the original request, reply + cross-group push', '2026-08-04 17:39:29.64121+00');
INSERT INTO public.workflow VALUES ('c2000000-0000-0000-0000-000000000004', 'confirm_outbound_completion', 'Validate target request, apply outbound storage txn, stub delivery PDF, complete the original request, reply + cross-group push', '2026-08-04 17:39:29.64121+00');
INSERT INTO public.workflow VALUES ('c2000000-0000-0000-0000-000000000005', 'view_storage', 'Query current storage balances, reply immediately', '2026-08-04 17:39:29.64121+00');
INSERT INTO public.workflow VALUES ('c2000000-0000-0000-0000-000000000006', 'view_storage_history', 'Query storage transaction history for a month, reply immediately', '2026-08-04 17:39:29.64121+00');
INSERT INTO public.workflow VALUES ('c2000000-0000-0000-0000-000000000007', 'adjust_storage', 'Apply standalone storage adjustments, reply', '2026-08-04 17:39:29.64121+00');
INSERT INTO public.workflow VALUES ('c2000000-0000-0000-0000-000000000008', 'recount_storage', 'Diff a full inventory snapshot against current balances and apply, reply', '2026-08-04 17:39:29.64121+00');
INSERT INTO public.workflow VALUES ('c2000000-0000-0000-0000-000000000009', 'move_storage', 'Apply internal repackaging moves, reply', '2026-08-04 17:39:29.64121+00');
INSERT INTO public.workflow VALUES ('c2000000-0000-0000-0000-00000000000a', 'upsert_address', 'Create or update a U-Choice address, reply', '2026-08-04 17:39:29.64121+00');
INSERT INTO public.workflow VALUES ('c2000000-0000-0000-0000-00000000000b', 'role_change', 'Apply a member role change, reply', '2026-08-04 17:39:29.64121+00');
INSERT INTO public.workflow VALUES ('c2000000-0000-0000-0000-00000000000c', 'view_invoice', 'Compute and reply with the monthly warehouse invoice, reply immediately', '2026-08-04 17:39:29.64121+00');
INSERT INTO public.workflow VALUES ('c2000000-0000-0000-0000-00000000000d', 'explain_service', 'Look up a service''s stored description/keywords and reply verbatim, immediately', '2026-08-05 05:34:38.703753+00');


--
-- Data for Name: workflow_step; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.workflow_step VALUES ('2a1d514f-a9ae-417c-a3c7-13c96b4f6b7c', 'af000001-0000-0000-0000-000000000005', 1, 'create_fedex_label', '{"carrier": "fedex"}');
INSERT INTO public.workflow_step VALUES ('01019462-8352-4960-b947-46e96ca81abe', 'af000001-0000-0000-0000-000000000005', 2, 'oms_create_workorder', '{}');
INSERT INTO public.workflow_step VALUES ('ea82fd31-eabb-495d-8749-56992ca4d4b0', 'af000001-0000-0000-0000-000000000005', 3, 'reply_wechat', '{}');
INSERT INTO public.workflow_step VALUES ('3bcccba9-c02d-4396-8fb0-6d84e1d74de2', 'af000001-0000-0000-0000-000000000004', 1, 'create_ups_label', '{"carrier": "ups"}');
INSERT INTO public.workflow_step VALUES ('619f432f-2c4d-44f2-97fe-d2e41711758b', 'af000001-0000-0000-0000-000000000004', 2, 'reply_wechat', '{}');
INSERT INTO public.workflow_step VALUES ('7332228a-d6c3-49c1-8955-f860bcb022b9', 'c2000000-0000-0000-0000-000000000001', 1, 'record_uchoice_request', '{}');
INSERT INTO public.workflow_step VALUES ('3270666d-974f-4ef0-b161-bb2d11f365e8', 'c2000000-0000-0000-0000-000000000001', 2, 'reply_wechat', '{}');
INSERT INTO public.workflow_step VALUES ('d9126326-884d-4d66-959e-4f4410748d87', 'c2000000-0000-0000-0000-000000000002', 1, 'record_uchoice_request', '{}');
INSERT INTO public.workflow_step VALUES ('7aa1763e-d547-4700-8a96-9cc8c3ac68fb', 'c2000000-0000-0000-0000-000000000002', 2, 'reply_wechat', '{}');
INSERT INTO public.workflow_step VALUES ('28bb6b4e-ab07-4633-a83b-bf68ec8159dc', 'c2000000-0000-0000-0000-000000000003', 1, 'lookup_and_validate_completion', '{"direction": "inbound"}');
INSERT INTO public.workflow_step VALUES ('000f6e1f-d1b8-4bc8-a735-9ad7441a7c8d', 'c2000000-0000-0000-0000-000000000003', 2, 'apply_inbound_storage_txn', '{}');
INSERT INTO public.workflow_step VALUES ('7d2eb044-2e3e-4b12-8cbc-27928bf9adf7', 'c2000000-0000-0000-0000-000000000003', 3, 'generate_pdf_stub', '{"doc_type": "receiving_confirmation"}');
INSERT INTO public.workflow_step VALUES ('044ffa87-930e-4a74-ad80-739eee3a3792', 'c2000000-0000-0000-0000-000000000003', 4, 'complete_existing_request', '{}');
INSERT INTO public.workflow_step VALUES ('9cebe95c-8e2d-4cdc-b3e0-c03bc5a0e656', 'c2000000-0000-0000-0000-000000000003', 5, 'reply_wechat', '{}');
INSERT INTO public.workflow_step VALUES ('3b98d79c-7b6d-4c1f-b41e-2760f191bacf', 'c2000000-0000-0000-0000-000000000004', 1, 'lookup_and_validate_completion', '{"direction": "outbound"}');
INSERT INTO public.workflow_step VALUES ('6de9eff3-ee7a-4838-b825-23b55dddae02', 'c2000000-0000-0000-0000-000000000004', 2, 'apply_outbound_storage_txn', '{}');
INSERT INTO public.workflow_step VALUES ('32973185-0580-4856-804d-a9bcbb96e718', 'c2000000-0000-0000-0000-000000000004', 3, 'generate_pdf_stub', '{"doc_type": "delivery_confirmation"}');
INSERT INTO public.workflow_step VALUES ('cfb62713-28a4-46ef-ae72-e941b9de656b', 'c2000000-0000-0000-0000-000000000004', 4, 'complete_existing_request', '{}');
INSERT INTO public.workflow_step VALUES ('3773ef26-19f4-47a3-8bcb-9114e3d50703', 'c2000000-0000-0000-0000-000000000004', 5, 'reply_wechat', '{}');
INSERT INTO public.workflow_step VALUES ('b779dd1e-64ed-4e67-be11-2f385ecd21a7', 'c2000000-0000-0000-0000-000000000005', 1, 'query_storage', '{}');
INSERT INTO public.workflow_step VALUES ('e38174b5-eb2f-4d17-91a4-00d58f85e98d', 'c2000000-0000-0000-0000-000000000005', 2, 'reply_wechat', '{}');
INSERT INTO public.workflow_step VALUES ('61533e49-b3ba-4968-b7be-4fec88bd7409', 'c2000000-0000-0000-0000-000000000006', 1, 'query_storage_history', '{}');
INSERT INTO public.workflow_step VALUES ('85c3352f-45ce-4823-a4d9-d752a5987a3b', 'c2000000-0000-0000-0000-000000000006', 2, 'reply_wechat', '{}');
INSERT INTO public.workflow_step VALUES ('65908a69-cb5a-4ed3-b09e-c5bc2fc77029', 'c2000000-0000-0000-0000-000000000007', 1, 'adjust_storage_txn', '{}');
INSERT INTO public.workflow_step VALUES ('63687a00-9cb7-4b54-be05-df05a0d6f4d4', 'c2000000-0000-0000-0000-000000000007', 2, 'reply_wechat', '{}');
INSERT INTO public.workflow_step VALUES ('17ca5c1a-fb03-410a-8b18-dbcd6f857ec2', 'c2000000-0000-0000-0000-000000000008', 1, 'recount_storage_txn', '{}');
INSERT INTO public.workflow_step VALUES ('c8660694-ae04-4801-9ac7-971f219cc2f0', 'c2000000-0000-0000-0000-000000000008', 2, 'reply_wechat', '{}');
INSERT INTO public.workflow_step VALUES ('df20906c-46bd-4d60-af4f-7e09c175ae88', 'c2000000-0000-0000-0000-000000000009', 1, 'move_storage_txn', '{}');
INSERT INTO public.workflow_step VALUES ('735ca00f-803e-4166-97b0-db48ea0ce219', 'c2000000-0000-0000-0000-000000000009', 2, 'reply_wechat', '{}');
INSERT INTO public.workflow_step VALUES ('57baaaeb-c241-4c9f-9527-f779d64ebcde', 'c2000000-0000-0000-0000-00000000000a', 1, 'upsert_address', '{}');
INSERT INTO public.workflow_step VALUES ('5cab71d9-1a45-449b-b2a6-73d4de89cf66', 'c2000000-0000-0000-0000-00000000000a', 2, 'reply_wechat', '{}');
INSERT INTO public.workflow_step VALUES ('acf6aacf-00be-484e-8ca7-6b40ba651a33', 'c2000000-0000-0000-0000-00000000000b', 1, 'apply_role_change', '{}');
INSERT INTO public.workflow_step VALUES ('4226cb79-1686-411f-b7c7-bec2cd69173a', 'c2000000-0000-0000-0000-00000000000b', 2, 'reply_wechat', '{}');
INSERT INTO public.workflow_step VALUES ('3b6307e9-a80e-4b1d-b8a6-319af29dc4aa', 'c2000000-0000-0000-0000-00000000000c', 1, 'compute_invoice_handler', '{}');
INSERT INTO public.workflow_step VALUES ('370f4da2-8e6a-4fc8-a165-24f790bf2f64', 'c2000000-0000-0000-0000-00000000000c', 2, 'reply_wechat', '{}');
INSERT INTO public.workflow_step VALUES ('385f3086-fc6a-4885-8cd5-7135868b11e8', 'c2000000-0000-0000-0000-00000000000d', 1, 'explain_service', '{}');
INSERT INTO public.workflow_step VALUES ('cde6538e-9682-4202-b51b-04f2ba1dcc93', 'c2000000-0000-0000-0000-00000000000d', 2, 'reply_wechat', '{}');


--
-- PostgreSQL database dump complete
--




-- ── Real group onboarding (group_config, group_member, group_service, group_service_role) ──

--
-- PostgreSQL database dump
--


-- Dumped from database version 18.4 (Debian 18.4-1.pgdg12+1)
-- Dumped by pg_dump version 18.3

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Data for Name: group_config; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.group_config VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A', 'U-Choice main group', true, NULL, 'null', '2026-08-04 18:21:52.859376+00', '2026-08-04 18:21:52.859376+00', NULL);


--
-- Data for Name: group_member; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.group_member VALUES ('transworld', 'd2a2a444-060e-4988-8167-0d4b468113b0', '69c72ff1-410b-4f83-a81a-b8687424adf7', 'Simon', true, '2026-08-04 18:22:59.567583+00', '2026-08-04 18:22:59.567583+00', NULL);


--
-- Data for Name: group_service; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.group_service VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000001', 'c2000000-0000-0000-0000-000000000001', '{}');
INSERT INTO public.group_service VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000002', 'c2000000-0000-0000-0000-000000000002', '{}');
INSERT INTO public.group_service VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000003', 'c2000000-0000-0000-0000-000000000003', '{}');
INSERT INTO public.group_service VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000004', 'c2000000-0000-0000-0000-000000000004', '{}');
INSERT INTO public.group_service VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000005', 'c2000000-0000-0000-0000-000000000005', '{}');
INSERT INTO public.group_service VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000006', 'c2000000-0000-0000-0000-000000000006', '{}');
INSERT INTO public.group_service VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000007', 'c2000000-0000-0000-0000-000000000007', '{}');
INSERT INTO public.group_service VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000008', 'c2000000-0000-0000-0000-000000000008', '{}');
INSERT INTO public.group_service VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000009', 'c2000000-0000-0000-0000-000000000009', '{}');
INSERT INTO public.group_service VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-00000000000a', 'c2000000-0000-0000-0000-00000000000a', '{}');
INSERT INTO public.group_service VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-00000000000b', 'c2000000-0000-0000-0000-00000000000b', '{}');
INSERT INTO public.group_service VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-00000000000c', 'c2000000-0000-0000-0000-00000000000c', '{}');
INSERT INTO public.group_service VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-00000000000d', 'c2000000-0000-0000-0000-00000000000d', '{}');


--
-- Data for Name: group_service_role; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.group_service_role VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000001', '69c72ff1-410b-4f83-a81a-b8687424adf7', 'transworld', '2026-08-04 18:23:18.953842+00');
INSERT INTO public.group_service_role VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000002', '69c72ff1-410b-4f83-a81a-b8687424adf7', 'transworld', '2026-08-04 18:23:19.131129+00');
INSERT INTO public.group_service_role VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000003', '69c72ff1-410b-4f83-a81a-b8687424adf7', 'transworld', '2026-08-04 18:23:19.363503+00');
INSERT INTO public.group_service_role VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000004', '69c72ff1-410b-4f83-a81a-b8687424adf7', 'transworld', '2026-08-04 18:23:19.533505+00');
INSERT INTO public.group_service_role VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000005', '69c72ff1-410b-4f83-a81a-b8687424adf7', 'transworld', '2026-08-04 18:23:19.723742+00');
INSERT INTO public.group_service_role VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000006', '69c72ff1-410b-4f83-a81a-b8687424adf7', 'transworld', '2026-08-04 18:23:19.911785+00');
INSERT INTO public.group_service_role VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000007', '69c72ff1-410b-4f83-a81a-b8687424adf7', 'transworld', '2026-08-04 18:23:20.088506+00');
INSERT INTO public.group_service_role VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000008', '69c72ff1-410b-4f83-a81a-b8687424adf7', 'transworld', '2026-08-04 18:23:20.266573+00');
INSERT INTO public.group_service_role VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000009', '69c72ff1-410b-4f83-a81a-b8687424adf7', 'transworld', '2026-08-04 18:23:20.441563+00');
INSERT INTO public.group_service_role VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-00000000000a', '69c72ff1-410b-4f83-a81a-b8687424adf7', 'transworld', '2026-08-04 18:23:20.631285+00');
INSERT INTO public.group_service_role VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-00000000000b', '69c72ff1-410b-4f83-a81a-b8687424adf7', 'transworld', '2026-08-04 18:23:20.844178+00');
INSERT INTO public.group_service_role VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-00000000000c', '69c72ff1-410b-4f83-a81a-b8687424adf7', 'transworld', '2026-08-04 18:23:21.027487+00');
INSERT INTO public.group_service_role VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000001', 'e640c95d-8e1f-429e-8436-c93a79dc585a', 'test', '2026-08-04 22:50:22.431758+00');
INSERT INTO public.group_service_role VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000001', '3e8e58c5-38a3-4dd2-8972-1a5a2a979d0b', 'test', '2026-08-04 22:50:22.507669+00');
INSERT INTO public.group_service_role VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000003', 'e640c95d-8e1f-429e-8436-c93a79dc585a', 'test', '2026-08-04 22:50:22.609801+00');
INSERT INTO public.group_service_role VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000003', '3e8e58c5-38a3-4dd2-8972-1a5a2a979d0b', 'test', '2026-08-04 22:50:22.719879+00');
INSERT INTO public.group_service_role VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000005', 'e640c95d-8e1f-429e-8436-c93a79dc585a', 'test', '2026-08-04 22:50:22.809735+00');
INSERT INTO public.group_service_role VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-000000000005', '3e8e58c5-38a3-4dd2-8972-1a5a2a979d0b', 'test', '2026-08-04 22:50:22.911028+00');
INSERT INTO public.group_service_role VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-00000000000d', '69c72ff1-410b-4f83-a81a-b8687424adf7', 'system', '2026-08-05 05:35:18.021998+00');
INSERT INTO public.group_service_role VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-00000000000d', '3e8e58c5-38a3-4dd2-8972-1a5a2a979d0b', 'system', '2026-08-05 05:35:18.021998+00');
INSERT INTO public.group_service_role VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-00000000000d', 'e640c95d-8e1f-429e-8436-c93a79dc585a', 'system', '2026-08-05 05:35:18.021998+00');
INSERT INTO public.group_service_role VALUES ('d2a2a444-060e-4988-8167-0d4b468113b0', 'c1000000-0000-0000-0000-00000000000d', '7544f868-bdc8-4c12-addd-2119b6bf583e', 'system', '2026-08-05 05:35:18.021998+00');


--
-- PostgreSQL database dump complete
--


