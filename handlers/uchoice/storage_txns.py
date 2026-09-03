"""
Storage-mutating handlers — each computes its own deltas from
context["collected_fields"] (this turn's inputs) and/or context["_uchoice_target"]
(the original request being completed, for the two completion handlers), then
calls core.uchoice_storage.apply_storage_delta once per delta. That shared
function is the only place that actually writes uchoice_storage/
uchoice_storage_txn rows.
"""
from handlers.base import BaseHandler
from core.uchoice_storage import (
    acquire_storage_scopes,
    allocate_box_picks,
    apply_storage_delta,
    apply_loose_pick,
)
from core.uchoice_rates import UNPACKING_FLAT, PALLETIZATION_PER_PALLET, CHARGE_TYPE_RATES


def _actor_id(context: dict) -> str:
    """Return the durable audit actor for either supported channel."""
    actor = context.get("wechat_openid") or context.get("submitted_by_staff_id")
    if not actor:
        raise RuntimeError("Storage mutation is missing an authenticated actor.")
    return str(actor)


def _require_caller_warehouse_scope(context: dict, warehouse_code: str | None) -> None:
    """
    Execution-time backstop, matching the pre-confirm
    core.pre_confirm_validators._valid_caller_warehouse_scope check --
    closes the gap between that check and a later confirm-turn actually
    executing it (same reasoning already used for role_change's handler
    re-checking what its own pre-confirm validator already checked). A
    caller with warehouse_codes=None is genuinely unscoped and never
    blocked here.
    """
    allowed = context.get("warehouse_codes")
    if allowed is not None and warehouse_code and warehouse_code not in allowed:
        raise RuntimeError("该仓库不在您的权限范围内。")


class ApplyInboundStorageHandler(BaseHandler):
    """confirm_inbound_completion, step 2."""

    def handle(self, context: dict, config: dict, db) -> dict:
        target = context.get("_uchoice_target", {})
        warehouse_code = target.get("warehouse_code")
        original_fields = target.get("original_fields", {})
        received_lines = context["collected_fields"].get("received_lines") or original_fields.get("sku_lines", [])
        request_log_id = context.get("request_log_id")
        created_by = _actor_id(context)

        acquire_storage_scopes(db, [
            (warehouse_code, line.get("sku_code")) for line in received_lines if line.get("sku_code")
        ])

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
        created_by = _actor_id(context)

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

        if context.get("source_channel") == "kefu":
            return self._handle_kefu_box_level(
                context=context,
                db=db,
                fulfillment_lines=fulfillment_lines,
                warehouse_code=warehouse_code,
                destination_warehouse_code=destination_warehouse_code,
                request_log_id=request_log_id,
                created_by=created_by,
                transportation_fee=transportation_fee,
                transfer_note=transfer_note,
                original_fields=original_fields,
            )

        smart_scopes = [(warehouse_code, line.get("sku_code")) for line in fulfillment_lines if line.get("sku_code")]
        if destination_warehouse_code:
            smart_scopes += [(destination_warehouse_code, line.get("sku_code")) for line in fulfillment_lines if line.get("sku_code")]
        acquire_storage_scopes(db, smart_scopes)

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

    @staticmethod
    def _line_box_count(line: dict) -> int:
        if line.get("picks"):
            return sum(p["box_count"] for p in line["picks"])
        if line.get("box_count") is not None:
            return line["box_count"]
        return line["boxes_per_pallet"] * line["pallet_count"]

    def _handle_kefu_box_level(
        self, *, context, db, fulfillment_lines, warehouse_code,
        destination_warehouse_code, request_log_id, created_by,
        transportation_fee, transfer_note, original_fields,
    ) -> dict:
        """Consume total boxes independently from requested/final packing."""
        required: dict[str, int] = {}
        explicit: dict[str, list[dict]] = {}
        for line in fulfillment_lines:
            sku = line.get("sku_code")
            if not sku:
                raise RuntimeError("出库明细缺少商品编号（sku_code），无法执行出库。")
            count = self._line_box_count(line)
            if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
                raise RuntimeError(f"商品 {sku} 的实际出库箱数必须为正整数。")
            required[sku] = required.get(sku, 0) + count
            if line.get("picks"):
                explicit.setdefault(sku, []).extend(line["picks"])

        destination_packing = context["collected_fields"].get("destination_packing_lines")
        if destination_warehouse_code:
            if destination_packing is None:
                if any(line.get("box_count") is not None or line.get("picks") for line in fulfillment_lines):
                    raise RuntimeError("内部调仓的散箱/取货明细必须同时说明目的仓装托方式。")
                destination_packing = [
                    {k: line[k] for k in ("sku_code", "boxes_per_pallet", "pallet_count")}
                    for line in fulfillment_lines
                ]
            packing_totals: dict[str, int] = {}
            for line in destination_packing:
                sku, bpp, pallets = line.get("sku_code"), line.get("boxes_per_pallet"), line.get("pallet_count")
                if not sku or not isinstance(bpp, int) or isinstance(bpp, bool) or bpp <= 0 or not isinstance(pallets, int) or isinstance(pallets, bool) or pallets <= 0:
                    raise RuntimeError("目的仓装托明细必须包含有效商品、每托箱数和托盘数。")
                packing_totals[sku] = packing_totals.get(sku, 0) + bpp * pallets
            if packing_totals != required:
                raise RuntimeError("目的仓装托箱数必须与实际出库箱数逐商品完全一致。")

        scopes = [(warehouse_code, sku) for sku in required]
        if destination_warehouse_code:
            scopes += [(destination_warehouse_code, sku) for sku in required]
        locked_rows = acquire_storage_scopes(db, scopes)
        origin_rows = {
            sku: [r for r in locked_rows if r.warehouse_code == warehouse_code and r.sku_code == sku]
            for sku in required
        }

        picks_by_sku: dict[str, list[dict]] = {}
        shortages = []
        for sku, needed in sorted(required.items()):
            if explicit.get(sku):
                aggregated: dict[int, int] = {}
                for pick in explicit[sku]:
                    bpp, count = pick.get("source_boxes_per_pallet"), pick.get("box_count")
                    if not isinstance(bpp, int) or isinstance(bpp, bool) or bpp <= 0 or not isinstance(count, int) or isinstance(count, bool) or count <= 0:
                        raise RuntimeError(f"商品 {sku} 的来源取货明细无效。")
                    aggregated[bpp] = aggregated.get(bpp, 0) + count
                picks = [{"source_boxes_per_pallet": bpp, "box_count": count} for bpp, count in sorted(aggregated.items())]
                if sum(p["box_count"] for p in picks) != needed:
                    raise RuntimeError(f"商品 {sku} 的来源取货箱数与实际出库箱数不一致。")
                available_by_bpp = {r.boxes_per_pallet: max(0, r.pallet_count) * r.boxes_per_pallet for r in origin_rows[sku]}
                if any(available_by_bpp.get(p["source_boxes_per_pallet"], 0) < p["box_count"] for p in picks):
                    picks = None
            else:
                picks = allocate_box_picks(origin_rows[sku], needed)
            if picks is None:
                available = sum(max(0, r.pallet_count) * r.boxes_per_pallet for r in origin_rows[sku])
                shortages.append({"sku_code": sku, "requested_boxes": needed, "available_boxes": available})
            else:
                picks_by_sku[sku] = picks

        if shortages:
            return {"_kefu_stop_workflow": "stock_changed", "stock_shortages": shortages}

        origin_txn_type = "transfer_out" if destination_warehouse_code else "outbound"
        applied = []
        for sku, picks in sorted(picks_by_sku.items()):
            for pick in picks:
                apply_loose_pick(
                    db, warehouse_code, sku, pick["source_boxes_per_pallet"], pick["box_count"],
                    request_log_id, created_by, origin_txn_type=origin_txn_type,
                    destination_warehouse_code=None, transfer_note=transfer_note,
                )
            applied.append({"sku_code": sku, "picks": picks})

        if destination_warehouse_code:
            for line in destination_packing:
                apply_storage_delta(
                    db, destination_warehouse_code, line["sku_code"], line["boxes_per_pallet"],
                    line["pallet_count"], "transfer_in", request_log_id,
                    note=transfer_note, created_by=created_by,
                )

        new_pallet_count = original_fields.get("new_pallet_count") or 0
        return {
            "fulfillment_lines": fulfillment_lines,
            "source_picks": applied,
            "destination_packing_lines": destination_packing or [],
            "transportation_fee": transportation_fee,
            "palletization_fee": new_pallet_count * PALLETIZATION_PER_PALLET,
            "destination_warehouse_code": destination_warehouse_code,
        }


class AdjustStorageHandler(BaseHandler):
    """adjust_storage — standalone delta corrections, plural lines."""

    def handle(self, context: dict, config: dict, db) -> dict:
        fields = context.get("collected_fields", {})
        warehouse_code = fields.get("warehouse_code")
        _require_caller_warehouse_scope(context, warehouse_code)
        request_log_id = context.get("request_log_id")
        created_by = _actor_id(context)
        lines = fields.get("adjustment_lines", []) or []
        acquire_storage_scopes(db, [(warehouse_code, line.get("sku_code")) for line in lines if line.get("sku_code")])

        applied = []
        for line in lines:
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
        _require_caller_warehouse_scope(context, warehouse_code)
        request_log_id = context.get("request_log_id")
        created_by = _actor_id(context)

        reported = {
            (l["sku_code"], l["boxes_per_pallet"]): l["pallet_count"]
            for l in fields.get("inventory_lines", []) or []
        }
        current_rows = db.query(UchoiceStorage).filter_by(warehouse_code=warehouse_code).all()
        # Existing SKUs omitted from a recount are also mutation scopes; take
        # the complete union in one sorted acquisition before the first delta.
        acquire_storage_scopes(db, [(warehouse_code, r.sku_code) for r in current_rows] + [(warehouse_code, sku) for sku, _ in reported])
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
    two convert-out/convert-in pairs: the source bucket loses box_count_moved
    boxes (its old bucket -1 pallet, a new/adjusted bucket at the reduced
    count +1 pallet -- skipped entirely when the reduced count is zero,
    since an entire pallet's worth was moved and there's nothing left on
    that specific pallet to track as a separate bucket), and the target
    bucket gains them symmetrically (always non-zero, since it's a sum of
    two positive counts).
    """

    def handle(self, context: dict, config: dict, db) -> dict:
        fields = context.get("collected_fields", {})
        warehouse_code = fields.get("warehouse_code")
        _require_caller_warehouse_scope(context, warehouse_code)
        request_log_id = context.get("request_log_id")
        created_by = _actor_id(context)
        lines = fields.get("move_lines", []) or []
        acquire_storage_scopes(db, [(warehouse_code, line.get("sku_code")) for line in lines if line.get("sku_code")])

        applied = []
        for line in lines:
            sku = line["sku_code"]
            source_bpp = line["source_boxes_per_pallet"]
            box_count_moved = line["box_count_moved"]
            target_bpp = line["target_boxes_per_pallet"]

            new_source_bpp = source_bpp - box_count_moved
            new_target_bpp = target_bpp + box_count_moved

            apply_storage_delta(db, warehouse_code, sku, source_bpp, -1, "move_out",
                                 request_log_id, note="internal move", created_by=created_by)
            if new_source_bpp > 0:
                apply_storage_delta(db, warehouse_code, sku, new_source_bpp, 1, "move_in",
                                     request_log_id, note="internal move", created_by=created_by)
            apply_storage_delta(db, warehouse_code, sku, target_bpp, -1, "move_out",
                                 request_log_id, note="internal move", created_by=created_by)
            apply_storage_delta(db, warehouse_code, sku, new_target_bpp, 1, "move_in",
                                 request_log_id, note="internal move", created_by=created_by)

            applied.append({"sku_code": sku, "source": f"{source_bpp}->{new_source_bpp if new_source_bpp > 0 else '（整托移出）'}",
                             "target": f"{target_bpp}->{new_target_bpp}"})

        return {"move_lines": applied}
