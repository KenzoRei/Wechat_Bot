\encoding UTF8
-- ============================================================
-- V41: inbound container number (柜号) and container unpacking fee (拆柜费)
-- Logistics WeChat Bot Platform
-- Date: 2026-10-08
--
-- Design: docs/ai-collaboration/2026-10-unpacking-fee/plan.md (rev 5).
--
-- uchoice_inbound_request: container_number becomes required (a 柜号, or
-- 无); the needs_unpacking yes/no field is dropped. warehouse_code stays
-- optional (JFK default since V9).
-- confirm_inbound_completion: optional unpacking_fee (set by the warehouse
-- at receipt; required by code when the request has a 柜号) and optional
-- container_number (the warehouse may supply one).
--
-- Definitions only: no grants, no data changes. Requests created before
-- V41 keep their stored fields; the code treats them as having no 柜号
-- ($0 unless the warehouse enters a fee). Each statement sets the whole
-- input_schema, so a rerun is harmless.
--
-- Apply in the same window as the v1.8.0 deploy: the old code with this
-- schema would ask Kefu users for container_number by its raw field name.
-- ============================================================

UPDATE service_type
SET description = '客户提交入库申请，告知仓库将有商品送到仓库，说明商品种类、数量以及柜号（没有柜号则为"无"）。提交后进入待处理状态，库存不会立即变动，需仓库人员实际收货并确认后才更新。',
    input_schema = '{
      "required": ["sku_lines", "container_number"],
      "optional": ["warehouse_code"],
      "field_hints": {
        "sku_lines": "Array of line items. Each is either palletized {sku_code, boxes_per_pallet, pallet_count} or loose {sku_code, box_count}.",
        "warehouse_code": "JFK, DE, or NJ",
        "container_number": "The shipping container number (柜号) exactly as the user typed it, e.g. MSCU1234567 -- never complete, correct or guess it. \"无\" when the user says there is no container. Always ask."
      }
    }'::jsonb
WHERE name = 'uchoice_inbound_request';

UPDATE service_type
SET input_schema = '{
      "required": ["reference_serial"],
      "optional": ["received_lines", "unpacking_fee", "container_number"],
      "field_hints": {
        "received_lines": "What was physically received. Defaults to the original request''s sku_lines for palletized lines if unstated. Loose-type lines always require explicit restatement — there is no sensible default for what a warehouseman physically received.",
        "reference_serial": "The pending inbound request being completed. If omitted, fuzzy-match against the injected candidate list of this warehouseman''s own pending inbound requests. 0 candidates: tell the user nothing is pending. 1: proceed. Multiple: list them and ask which one.",
        "unpacking_fee": "Container unpacking fee (拆柜费) in USD, as the exact number the warehouse typed (no rounding, no Chinese numerals). 0 when they say no fee. The system asks for it when the request has a container number.",
        "container_number": "Only when the warehouse gives a container number (柜号) at receipt, exactly as typed."
      }
    }'::jsonb
WHERE name = 'confirm_inbound_completion';
