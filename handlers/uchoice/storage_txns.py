"""
Storage-mutating handlers — each computes its own deltas from
context["collected_fields"] (this turn's inputs) and/or context["_uchoice_target"]
(the original request being completed, for the two completion handlers), then
calls core.uchoice_storage.apply_storage_delta once per delta. That shared
function is the only place that actually writes uchoice_storage/
uchoice_storage_txn rows.
"""
from handlers.base import BaseHandler
from core.uchoice_storage import apply_storage_delta, apply_loose_pick
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
            sku = line.get("sku_code")
            if not sku:
                raise RuntimeError("入库明细缺少商品编号（sku_code），无法执行入库。")
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

        # If the destination resolves to one of our own two warehouses (not
        # an external customer), this is an inter-warehouse transfer: one
        # confirmation moves storage on both sides — origin decreases here,
        # destination increases in the same step below — rather than a
        # second separate inbound request the destination warehouseman would
        # otherwise have to confirm on their own.
        transportation_fee = 0
        destination_warehouse_code = None
        destination_address_id = original_fields.get("destination_address_id")
        if destination_address_id:
            from models.uchoice import UchoiceAddress
            addr = db.query(UchoiceAddress).filter_by(address_id=destination_address_id).first()
            if addr:
                transportation_fee = CHARGE_TYPE_RATES.get(addr.charge_type, 0)
                destination_warehouse_code = addr.destination_warehouse_code

        origin_txn_type = "transfer_out" if destination_warehouse_code else "outbound"
        dest_txn_type = "transfer_in"
        transfer_note = f"transfer to {destination_warehouse_code}" if destination_warehouse_code else None

        applied = []
        for line in fulfillment_lines:
            sku = line.get("sku_code")
            if not sku:
                raise RuntimeError("出库明细缺少商品编号（sku_code），无法执行出库。")
            if "picks" in line:
                # Loose-box fulfillment — each pick draws box_count boxes
                # from a specific source bucket (either stated explicitly by
                # the warehouseman, or auto-resolved by workflow_engine's
                # _resolve_outbound_loose_pick_defaults against the smallest
                # available buckets first). apply_loose_pick derives the
                # leftover-pallet math itself; nothing here needs to be told
                # what remains on a partially-picked pallet.
                picks_applied = []
                for pick in line["picks"]:
                    source_bpp = pick["source_boxes_per_pallet"]
                    box_count = pick["box_count"]
                    apply_loose_pick(
                        db, warehouse_code, sku, source_bpp, box_count, request_log_id,
                        created_by, origin_txn_type=origin_txn_type,
                        destination_warehouse_code=destination_warehouse_code,
                        transfer_note=transfer_note,
                    )
                    picks_applied.append({"source_boxes_per_pallet": source_bpp, "box_count": box_count})
                applied.append({"sku_code": sku, "picks": picks_applied})
            elif "box_count" in line:
                # No picks were resolved for this loose line — the
                # pre-confirm validator should have caught this (insufficient
                # stock to auto-default) before confirmation was ever shown.
                # Fail loudly rather than guessing if it somehow slipped through.
                raise RuntimeError(
                    f"商品 {sku} 为散箱出库，缺少托盘取货明细，无法执行。"
                )
            else:
                bpp = line.get("boxes_per_pallet")
                if bpp is None:
                    raise RuntimeError(
                        f"商品 {sku} 缺少每托箱数（boxes_per_pallet），且该仓库无现有库存可作为默认值，"
                        f"无法确定发货数量，请明确指定。"
                    )
                qty = line["pallet_count"]
                apply_storage_delta(
                    db, warehouse_code, sku, bpp, -qty, origin_txn_type, request_log_id,
                    note=transfer_note, created_by=created_by
                )
                applied.append({"sku_code": sku, "boxes_per_pallet": bpp, "pallet_count": qty})

                if destination_warehouse_code:
                    apply_storage_delta(
                        db, destination_warehouse_code, sku, bpp, qty, dest_txn_type,
                        request_log_id, note=transfer_note, created_by=created_by
                    )

        new_pallet_count = original_fields.get("new_pallet_count") or 0
        palletization_fee = new_pallet_count * PALLETIZATION_PER_PALLET

        return {
            "fulfillment_lines": applied,
            "transportation_fee": transportation_fee,
            "palletization_fee": palletization_fee,
            "destination_warehouse_code": destination_warehouse_code,
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
