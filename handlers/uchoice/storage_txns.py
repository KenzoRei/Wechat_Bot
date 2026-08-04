"""
Storage-mutating handlers — each computes its own deltas from
context["collected_fields"] (this turn's inputs) and/or context["_uchoice_target"]
(the original request being completed, for the two completion handlers), then
calls core.uchoice_storage.apply_storage_delta once per delta. That shared
function is the only place that actually writes uchoice_storage/
uchoice_storage_txn rows.
"""
from handlers.base import BaseHandler
from core.uchoice_storage import apply_storage_delta
from core.uchoice_rates import UNPACKING_FLAT, PALLETIZATION_PER_PALLET, CHARGE_TYPE_RATES


class ApplyInboundStorageHandler(BaseHandler):
    """confirm_inbound_completion, step 2."""

    def handle(self, context: dict, config: dict, db) -> dict:
        target = context.get("_uchoice_target", {})
        warehouse_code = target.get("warehouse_code")
        original_fields = target.get("original_fields", {})
        received_lines = context["collected_fields"].get("received_lines") or original_fields.get("sku_lines", [])
        request_log_id = context.get("request_log_id")
        created_by = context.get("wechat_openid")

        applied = []
        for line in received_lines:
            sku = line["sku_code"]
            if "box_count" in line:
                # No sensible default exists for loose-type lines — the design
                # doc requires explicit restatement of how they were received.
                raise RuntimeError(f"商品 {sku} 为散箱入库，必须明确说明装托方式，无法使用默认值。")
            bpp = line["boxes_per_pallet"]
            qty = line["pallet_count"]
            apply_storage_delta(
                db, warehouse_code, sku, bpp, qty, "inbound", request_log_id,
                note=None, created_by=created_by
            )
            applied.append({"sku_code": sku, "boxes_per_pallet": bpp, "pallet_count": qty})

        unpacking_fee = UNPACKING_FLAT if original_fields.get("needs_unpacking") else 0

        return {"received_lines": applied, "unpacking_fee": unpacking_fee}


class ApplyOutboundStorageHandler(BaseHandler):
    """confirm_outbound_completion, step 2."""

    def handle(self, context: dict, config: dict, db) -> dict:
        target = context.get("_uchoice_target", {})
        warehouse_code = target.get("warehouse_code")
        original_fields = target.get("original_fields", {})
        fulfillment_lines = context["collected_fields"].get("fulfillment_lines") or original_fields.get("sku_lines", [])
        request_log_id = context.get("request_log_id")
        created_by = context.get("wechat_openid")

        applied = []
        for line in fulfillment_lines:
            sku = line["sku_code"]
            if "source_boxes_per_pallet" in line and "resulting_boxes_per_pallet" in line:
                # loose-box convert pair — never defaulted, per the design doc
                source_bpp = line["source_boxes_per_pallet"]
                resulting_bpp = line["resulting_boxes_per_pallet"]
                apply_storage_delta(
                    db, warehouse_code, sku, source_bpp, -1, "convert_out",
                    request_log_id, note="loose-box fulfillment pick", created_by=created_by
                )
                apply_storage_delta(
                    db, warehouse_code, sku, resulting_bpp, 1, "convert_in",
                    request_log_id, note="loose-box fulfillment pick", created_by=created_by
                )
                applied.append({"sku_code": sku, "convert": f"{source_bpp}->{resulting_bpp}"})
            else:
                bpp = line["boxes_per_pallet"]
                qty = line["pallet_count"]
                apply_storage_delta(
                    db, warehouse_code, sku, bpp, -qty, "outbound", request_log_id,
                    note=None, created_by=created_by
                )
                applied.append({"sku_code": sku, "boxes_per_pallet": bpp, "pallet_count": qty})

        transportation_fee = 0
        destination_address_id = original_fields.get("destination_address_id")
        if destination_address_id:
            from models.uchoice import UchoiceAddress
            addr = db.query(UchoiceAddress).filter_by(address_id=destination_address_id).first()
            if addr:
                transportation_fee = CHARGE_TYPE_RATES.get(addr.charge_type, 0)

        new_pallet_count = original_fields.get("new_pallet_count") or 0
        palletization_fee = new_pallet_count * PALLETIZATION_PER_PALLET

        return {
            "fulfillment_lines": applied,
            "transportation_fee": transportation_fee,
            "palletization_fee": palletization_fee,
        }


class AdjustStorageHandler(BaseHandler):
    """adjust_storage — standalone delta corrections, plural lines."""

    def handle(self, context: dict, config: dict, db) -> dict:
        fields = context.get("collected_fields", {})
        warehouse_code = fields.get("warehouse_code")
        request_log_id = context.get("request_log_id")
        created_by = context.get("wechat_openid")

        applied = []
        for line in fields.get("adjustment_lines", []) or []:
            apply_storage_delta(
                db, warehouse_code, line["sku_code"], line["boxes_per_pallet"],
                line["pallet_delta"], "adjust", request_log_id,
                note=line.get("reason"), created_by=created_by
            )
            applied.append(line)

        return {"adjustment_lines": applied}


class RecountStorageHandler(BaseHandler):
    """
    recount_storage — diff-and-adjust against current balances, not a
    destructive wipe-and-rebuild. A bucket omitted from the reported snapshot
    means "now zero", per the design doc.
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        from models.uchoice import UchoiceStorage

        fields = context.get("collected_fields", {})
        warehouse_code = fields.get("warehouse_code")
        request_log_id = context.get("request_log_id")
        created_by = context.get("wechat_openid")

        reported = {
            (l["sku_code"], l["boxes_per_pallet"]): l["pallet_count"]
            for l in fields.get("inventory_lines", []) or []
        }
        current_rows = db.query(UchoiceStorage).filter_by(warehouse_code=warehouse_code).all()
        current = {(r.sku_code, r.boxes_per_pallet): r.pallet_count for r in current_rows}

        applied = []
        for key in sorted(set(reported) | set(current)):
            sku, bpp = key
            delta = reported.get(key, 0) - current.get(key, 0)
            if delta == 0:
                continue
            apply_storage_delta(
                db, warehouse_code, sku, bpp, delta, "recount", request_log_id,
                note="recount diff", created_by=created_by
            )
            applied.append({"sku_code": sku, "boxes_per_pallet": bpp, "delta": delta})

        return {"recount_deltas": applied}


class MoveStorageHandler(BaseHandler):
    """
    move_storage — internal repackaging, net-zero total boxes. Each line is
    two convert pairs (4 txn rows): the source bucket loses box_count_moved
    boxes (its old bucket -1 pallet, a new/adjusted bucket at the reduced
    count +1 pallet), and the target bucket gains them symmetrically.
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        fields = context.get("collected_fields", {})
        warehouse_code = fields.get("warehouse_code")
        request_log_id = context.get("request_log_id")
        created_by = context.get("wechat_openid")

        applied = []
        for line in fields.get("move_lines", []) or []:
            sku = line["sku_code"]
            source_bpp = line["source_boxes_per_pallet"]
            box_count_moved = line["box_count_moved"]
            target_bpp = line["target_boxes_per_pallet"]

            new_source_bpp = source_bpp - box_count_moved
            new_target_bpp = target_bpp + box_count_moved

            apply_storage_delta(db, warehouse_code, sku, source_bpp, -1, "move_out",
                                 request_log_id, note="internal move", created_by=created_by)
            apply_storage_delta(db, warehouse_code, sku, new_source_bpp, 1, "move_in",
                                 request_log_id, note="internal move", created_by=created_by)
            apply_storage_delta(db, warehouse_code, sku, target_bpp, -1, "move_out",
                                 request_log_id, note="internal move", created_by=created_by)
            apply_storage_delta(db, warehouse_code, sku, new_target_bpp, 1, "move_in",
                                 request_log_id, note="internal move", created_by=created_by)

            applied.append({"sku_code": sku, "source": f"{source_bpp}->{new_source_bpp}",
                             "target": f"{target_bpp}->{new_target_bpp}"})

        return {"move_lines": applied}
