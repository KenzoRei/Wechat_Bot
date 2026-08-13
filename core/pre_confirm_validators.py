"""
Pre-confirmation business-rule checks — run right before a confirmation
template would be built, so a request that was always going to fail doesn't
get shown a confirmation prompt at all. Registry-with-default-fallback,
mirroring handlers/registry.py's idiom; most services never need an entry.
"""
from sqlalchemy.orm import Session as DBSession
from models.group import GroupMember
from models.role import Role
from core.uchoice_validation import format_validation_issues, validate_sku_lines


def _compose(*validators):
    """Run validators in order and return the first blocking message."""

    def composed(context: dict, collected_fields: dict, db: DBSession) -> str | None:
        for validator in validators:
            error = validator(context, collected_fields, db)
            if error:
                return error
        return None

    return composed


def _valid_inbound_sku_lines(context: dict, collected_fields: dict, db: DBSession) -> str | None:
    """uchoice_inbound_request: reject missing or uncataloged line SKUs."""

    del context
    issues = validate_sku_lines(
        collected_fields.get("sku_lines"),
        field_name="sku_lines",
        db=db,
    )
    return format_validation_issues(issues)


def _positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _nonnegative_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _typed_line_issues(lines, checker) -> list[str]:
    """
    Run a per-line `checker(line) -> str | None` (a human-readable problem
    description, or None if the line is fine) over an already-list-shaped
    `lines`, returning one message per problem found. Does not duplicate
    validate_sku_lines's own shape/SKU checks -- callers compose this after
    that, only inspecting a line here if it's already a dict (a non-dict
    line already produced its own issue from validate_sku_lines).
    """
    messages = []
    for index, line in enumerate(lines or []):
        if not isinstance(line, dict):
            continue
        problem = checker(line)
        if problem:
            messages.append(f"第 {index + 1} 项：{problem}")
    return messages


def _valid_adjust_storage_lines(context: dict, collected_fields: dict, db: DBSession) -> str | None:
    """
    adjust_storage's typed contract (systemic-validation-addendum.md Sec 3a):
    valid positive bucket dimensions, non-zero integer delta. "No negative
    resulting balance" is authoritative-state-dependent -- already enforced
    at execution time by core.uchoice_storage.apply_storage_delta's own
    balance check against live data, not duplicated here (duplicating it
    pre-confirm would use possibly-stale state anyway).
    """
    del context
    field_name = "adjustment_lines"
    lines = collected_fields.get(field_name)
    sku_issues = validate_sku_lines(lines, field_name=field_name, db=db)
    if sku_issues:
        return format_validation_issues(sku_issues)
    if not isinstance(lines, list):
        return None

    def check(line: dict) -> str | None:
        if not _positive_int(line.get("boxes_per_pallet")):
            return "每托箱数（boxes_per_pallet）必须是正整数。"
        delta = line.get("pallet_delta")
        if not isinstance(delta, int) or isinstance(delta, bool) or delta == 0:
            return "调整数量（pallet_delta）必须是非零整数。"
        return None

    messages = _typed_line_issues(lines, check)
    return "请修正以下调整明细：" + "；".join(messages) if messages else None


def _valid_recount_storage_lines(context: dict, collected_fields: dict, db: DBSession) -> str | None:
    """
    recount_storage's typed contract: positive bucket dimensions,
    non-negative integer counts (zero is a legitimate reported count --
    recount_storage's own semantics treat an omitted bucket as zero, so an
    explicit zero must be just as valid), and unique (sku_code,
    boxes_per_pallet) bucket keys within one submission -- confirmed by
    direct read that handlers.uchoice.storage_txns.RecountStorageHandler
    builds `reported` via a dict comprehension keyed on exactly that pair,
    which silently keeps only the last of any duplicates and discards the
    rest of the customer's actual input (Codex round-32 finding).
    """
    del context
    field_name = "inventory_lines"
    lines = collected_fields.get(field_name)
    sku_issues = validate_sku_lines(
        lines, field_name=field_name, db=db,
        duplicate_key_fields=("sku_code", "boxes_per_pallet"),
    )
    if sku_issues:
        return format_validation_issues(sku_issues)
    if not isinstance(lines, list):
        return None

    def check(line: dict) -> str | None:
        if not _positive_int(line.get("boxes_per_pallet")):
            return "每托箱数（boxes_per_pallet）必须是正整数。"
        if not _nonnegative_int(line.get("pallet_count")):
            return "托数（pallet_count）必须是非负整数。"
        return None

    messages = _typed_line_issues(lines, check)
    return "请修正以下盘点明细：" + "；".join(messages) if messages else None


def _valid_move_storage_lines(context: dict, collected_fields: dict, db: DBSession) -> str | None:
    """
    move_storage's typed contract: positive/in-range quantities for both
    the source and target bucket dimensions. box_count_moved may equal
    source_boxes_per_pallet -- moving an entire pallet's worth away is
    valid; MoveStorageHandler skips creating a leftover bucket in that case
    (there's nothing left on that specific pallet to track) rather than
    rejecting it. It may never exceed source_boxes_per_pallet -- that would
    require boxes from more than one pallet, which this single-line
    contract can't express. Box conservation -- nothing enters or leaves
    the warehouse -- is structurally guaranteed by MoveStorageHandler
    always applying the same box_count_moved to both sides, not something
    a separate check needs to re-derive. Authoritative source-bucket
    existence/sufficiency is enforced by _move_storage_stock_available
    below (live incident: reaching apply_storage_delta's own balance check
    at execution time, after confirmation was already shown, produced a
    confusing "库存不足" failure with no indication of what buckets
    actually exist -- the confirmation must not promise a move that's
    already known to fail).
    """
    del context
    field_name = "move_lines"
    lines = collected_fields.get(field_name)
    sku_issues = validate_sku_lines(lines, field_name=field_name, db=db)
    if sku_issues:
        return format_validation_issues(sku_issues)
    if not isinstance(lines, list):
        return None

    def check(line: dict) -> str | None:
        source_bpp = line.get("source_boxes_per_pallet")
        target_bpp = line.get("target_boxes_per_pallet")
        box_count_moved = line.get("box_count_moved")
        if not _positive_int(source_bpp):
            return "源托盘规格（source_boxes_per_pallet）必须是正整数。"
        if not _positive_int(target_bpp):
            return "目标托盘规格（target_boxes_per_pallet）必须是正整数。"
        if not _positive_int(box_count_moved):
            return "移动箱数（box_count_moved）必须是正整数。"
        if isinstance(source_bpp, int) and isinstance(box_count_moved, int) and box_count_moved > source_bpp:
            return f"移动箱数（{box_count_moved}）不能大于源托盘规格（{source_bpp}）。"
        return None

    messages = _typed_line_issues(lines, check)
    return "请修正以下调拨明细：" + "；".join(messages) if messages else None


def _move_storage_stock_available(context: dict, collected_fields: dict, db: DBSession) -> str | None:
    """
    move_storage: the source (warehouse, sku, source_boxes_per_pallet)
    bucket must actually exist with at least one pallet, or the confirmed
    move will fail at execution time on apply_storage_delta's own balance
    check -- after already showing the staff a confirmation summary that
    promised it would work. Reports exactly what buckets DO exist for that
    SKU, which surfaces the common real mistake directly: a wrong pallet
    spec or the wrong SKU entirely (e.g. two similarly-named products each
    with their own bucket sizes).
    """
    from models.uchoice import UchoiceStorage
    from core.uchoice_context import sku_label_map

    warehouse_code = collected_fields.get("warehouse_code")
    lines = collected_fields.get("move_lines")
    if not warehouse_code or not isinstance(lines, list):
        return None

    sku_labels = None
    messages = []
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            continue
        sku = line.get("sku_code")
        source_bpp = line.get("source_boxes_per_pallet")
        if not sku or not _positive_int(source_bpp):
            continue
        bucket = db.query(UchoiceStorage).filter_by(
            warehouse_code=warehouse_code, sku_code=sku, boxes_per_pallet=source_bpp
        ).first()
        if bucket is not None and bucket.pallet_count >= 1:
            continue
        if sku_labels is None:
            sku_labels = sku_label_map(db)
        label = sku_labels.get(sku, sku)
        existing = (
            db.query(UchoiceStorage)
            .filter_by(warehouse_code=warehouse_code, sku_code=sku)
            .filter(UchoiceStorage.pallet_count > 0)
            .order_by(UchoiceStorage.boxes_per_pallet.asc())
            .all()
        )
        if existing:
            options = "、".join(f"{b.boxes_per_pallet}/托（现有{b.pallet_count}托）" for b in existing)
            messages.append(f"第 {index + 1} 项：{label} 在 {warehouse_code} 仓没有 {source_bpp}/托 的托盘，现有规格：{options}。")
        else:
            messages.append(f"第 {index + 1} 项：{label} 在 {warehouse_code} 仓当前没有库存，无法调拨。")

    return "请核实来源托盘规格：" + "；".join(messages) if messages else None


def _valid_completion_sku_lines(
    override_field: str,
):
    """Build a completion validator for effective lines and no substitution."""

    def validator(context: dict, collected_fields: dict, db: DBSession) -> str | None:
        del context
        reference_serial = collected_fields.get("reference_serial")
        if not reference_serial:
            return None

        from core.uchoice_context import resolve_completion_target

        target, original_fields = resolve_completion_target(db, reference_serial)
        if target is None:
            return None

        original_lines = original_fields.get("sku_lines") or []
        original_issues = validate_sku_lines(
            original_lines,
            field_name="original_request.sku_lines",
            db=db,
        )
        if original_issues:
            return format_validation_issues(original_issues)

        # Match the execution handlers' effective-line semantics exactly:
        # missing or empty overrides fall back to the original request lines.
        effective_lines = collected_fields.get(override_field) or original_lines
        effective_issues = validate_sku_lines(
            effective_lines,
            field_name=override_field,
            db=db,
        )
        if effective_issues:
            return format_validation_issues(effective_issues)

        original_skus = {line["sku_code"] for line in original_lines}
        substitutions = [
            index
            for index, line in enumerate(effective_lines)
            if line["sku_code"] not in original_skus
        ]
        if substitutions:
            positions = "、".join(str(index + 1) for index in substitutions)
            return (
                f"完成明细第 {positions} 项使用了原申请中没有的商品。"
                "仓库完成确认只能调整原商品的数量或包装方式，不能替换商品。"
            )
        return None

    return validator


_valid_inbound_completion_skus = _valid_completion_sku_lines("received_lines")
_valid_outbound_completion_skus = _valid_completion_sku_lines("fulfillment_lines")


def _valid_role_change_target_and_role(context: dict, collected_fields: dict, db: DBSession) -> str | None:
    """
    role_change pre-confirm boundary 2 (phase4-self-registration.md Sec 4,
    Codex round-37/round-41): recheck target membership, recheck new_role is
    in the server allowlist (pending excluded per Sec 5), and require a
    valid non-blank warehouse_code when promoting to warehouseman. Backstops
    the before-persistence boundary in core/workflow_engine.py -- this runs
    again here since pre-confirm can be reached via a continuation turn that
    didn't go through the same sanitizer call, and is itself backstopped by
    the execution-time check in handlers/uchoice/role_change.py.
    """
    from core.uchoice_constants import ASSIGNABLE_ROLE_NAMES, VALID_WAREHOUSE_CODES
    from core.role_identity import parse_target_identity

    target_openid = collected_fields.get("target_openid")
    if target_openid:
        identity = parse_target_identity(target_openid)
        if identity.kind == "kefu":
            from models.kefu import KefuStaff
            target_exists = db.query(KefuStaff).filter_by(
                staff_id=identity.key, group_id=context["group_id"]
            ).first() is not None
        else:
            target_exists = db.query(GroupMember).filter_by(
                wechat_openid=identity.key, group_id=context["group_id"]
            ).first() is not None
        if not target_exists:
            return "该成员不在本群组中，无法调整角色。"

    new_role = collected_fields.get("new_role")
    if new_role and new_role not in ASSIGNABLE_ROLE_NAMES:
        return f"未知角色：{new_role}"

    if new_role == "warehouseman":
        warehouse_code = collected_fields.get("warehouse_code")
        if not warehouse_code or warehouse_code not in VALID_WAREHOUSE_CODES:
            return "指派为仓库管理员需要提供有效的仓库代码（JFK 或 DE）。"

    return None


def _last_admin_protection(context: dict, collected_fields: dict, db: DBSession) -> str | None:
    """
    role_change: reject demoting the group's only remaining active admin.
    Promotions (new_role == 'admin') never trip this check.

    kefu-migration-plan.md Sec 2.3: admin is an ASSIGNABLE_ROLE_NAMES role
    reachable by either channel, so both the target lookup and the "how
    many admins remain" count must span GroupMember and kefu_staff
    together -- counting only one table would let the last real admin be
    demoted as long as an admin existed in the other table, or the
    reverse.
    """
    from core.role_identity import parse_target_identity
    from models.kefu import KefuStaff

    if collected_fields.get("new_role") == "admin":
        return None

    target_openid = collected_fields.get("target_openid")
    if not target_openid:
        return None

    group_id = context["group_id"]
    identity = parse_target_identity(target_openid)
    if identity is None:
        return None

    if identity.kind == "kefu":
        target_role_id = getattr(
            db.query(KefuStaff).filter_by(staff_id=identity.key, group_id=group_id).first(),
            "role_id", None,
        )
    else:
        target_role_id = getattr(
            db.query(GroupMember).filter_by(wechat_openid=identity.key, group_id=group_id).first(),
            "role_id", None,
        )
    if target_role_id is None:
        return None

    target_role = db.query(Role).filter_by(role_id=target_role_id).first()
    if not target_role or target_role.name != "admin":
        return None  # target isn't currently an admin — nothing to protect

    admin_role = db.query(Role).filter_by(name="admin").first()
    if not admin_role:
        return None

    smart_robot_admin_count = db.query(GroupMember).filter_by(
        group_id=group_id, role_id=admin_role.role_id, is_active=True
    ).count()
    kefu_admin_count = db.query(KefuStaff).filter_by(
        group_id=group_id, role_id=admin_role.role_id, is_active=True
    ).count()
    if (smart_robot_admin_count + kefu_admin_count) <= 1:
        return "无法将该成员的角色改为非管理员——该群组当前仅剩一名管理员。"
    return None


def _loose_outbound_pick_required(context: dict, collected_fields: dict, db: DBSession) -> str | None:
    """
    confirm_outbound_completion: a loose (box_count) original line gets an
    automatic pick resolved by core.kefu_turn_apply._resolve_outbound_
    completion_loose_picks / core.workflow_engine._resolve_outbound_loose_
    pick_defaults (smallest-boxes_per_pallet buckets first) before this
    validator runs. This only needs to block the rare case where that
    resolution failed -- total available stock across every bucket for
    this sku+warehouse can't even cover the requested amount -- so a
    request that was always going to crash on insufficient stock doesn't
    get shown a confirmation prompt at all.

    Reports the real bucket breakdown per SKU (same treatment as
    _move_storage_stock_available), not just "insufficient" -- a bare
    rejection gives the warehouseman nothing to act on; showing what's
    actually on hand (or that there's genuinely none) lets them decide
    whether to correct the request, do a partial fulfillment, or recount.
    """
    reference_serial = collected_fields.get("reference_serial")
    if not reference_serial:
        return None

    from models.uchoice import UchoiceStorage
    from core.uchoice_context import resolve_completion_target, sku_label_map

    target, original_fields = resolve_completion_target(db, reference_serial)
    if target is None:
        return None

    loose_lines = {
        l["sku_code"]: l["box_count"]
        for l in (original_fields.get("sku_lines") or []) if "box_count" in l
    }
    if not loose_lines:
        return None

    restated_lines = collected_fields.get("fulfillment_lines") or []
    by_sku = {l["sku_code"]: l for l in restated_lines}
    missing = [sku for sku in loose_lines if not (by_sku.get(sku) or {}).get("picks")]
    if not missing:
        return None

    warehouse_code = original_fields.get("warehouse_code")
    sku_labels = sku_label_map(db)
    messages = []
    for sku in sorted(missing):
        label = sku_labels.get(sku, sku)
        needed = loose_lines[sku]
        buckets = (
            db.query(UchoiceStorage)
            .filter_by(warehouse_code=warehouse_code, sku_code=sku)
            .filter(UchoiceStorage.pallet_count > 0)
            .order_by(UchoiceStorage.boxes_per_pallet.asc())
            .all()
        )
        if buckets:
            options = "、".join(f"{b.boxes_per_pallet}/托（现有{b.pallet_count}托）" for b in buckets)
            available_total = sum(b.boxes_per_pallet * b.pallet_count for b in buckets)
            messages.append(
                f"{label}：需要 {needed} 箱，{warehouse_code} 仓现有规格：{options}"
                f"（共 {available_total} 箱），不足以自动分配"
            )
        else:
            messages.append(f"{label}：需要 {needed} 箱，{warehouse_code} 仓当前没有库存")

    return "请核实以下商品库存：" + "；".join(messages) + "。或手动说明从哪些托盘规格各取多少箱。"


def _loose_inbound_restatement_required(context: dict, collected_fields: dict, db: DBSession) -> str | None:
    """
    confirm_inbound_completion: a loose (box_count) original line has no
    default — nothing exists yet to default from, since inbound creates new
    storage rather than drawing from existing buckets. The warehouseman must
    state how the received loose boxes were packed: boxes_per_pallet +
    pallet_count, matching exactly what ApplyInboundStorageHandler writes —
    this must stay in sync with that handler's expected shape (a source/
    resulting conversion-pair shape was tried here previously and would have
    crashed with a raw KeyError even after "restatement", since the handler
    never looked for those field names at all).
    """
    reference_serial = collected_fields.get("reference_serial")
    if not reference_serial:
        return None

    from core.uchoice_context import resolve_completion_target, sku_label_map

    target, original_fields = resolve_completion_target(db, reference_serial)
    if target is None:
        return None

    loose_skus = {l["sku_code"] for l in (original_fields.get("sku_lines") or []) if "box_count" in l}
    if not loose_skus:
        return None

    restated_lines = collected_fields.get("received_lines") or []
    by_sku = {l["sku_code"]: l for l in restated_lines}
    missing = [
        sku for sku in loose_skus
        if not ({"boxes_per_pallet", "pallet_count"} <= set((by_sku.get(sku) or {}).keys()))
    ]
    if not missing:
        return None

    sku_labels = sku_label_map(db)
    missing_labels = "、".join(sku_labels.get(s, s) for s in sorted(missing))
    return f"商品 {missing_labels} 是散箱入库，请说明打包成了多少箱/托、共多少托。"


def _valid_sku_line_quantities(field_name: str):
    """
    Build a validator requiring every line to be either a complete loose-box
    line (positive box_count) or a complete pallet line (positive
    boxes_per_pallet AND positive pallet_count) -- never a line missing part
    of either shape.

    validate_sku_lines (used by _valid_inbound_sku_lines/
    _valid_outbound_sku_lines) only checks sku_code shape/catalog
    membership -- it was never responsible for quantity fields, and unlike
    adjust_storage/recount_storage/move_storage there was no separate
    per-line quantity check for inbound/outbound at all. A line missing
    boxes_per_pallet reached the confirmation summary as a literal "?" and,
    when confirmed anyway, as a literal "None" in the customer-facing copy
    (core/kefu_customer_copy.py's f-string formats whatever's there) --
    observed live on REQ-20260812-001179 (10 托 with no boxes_per_pallet
    ever collected or asked for).
    """

    def validator(context: dict, collected_fields: dict, db: DBSession) -> str | None:
        del context
        lines = collected_fields.get(field_name)
        if not isinstance(lines, list):
            return None

        def check(line: dict) -> str | None:
            if "box_count" in line:
                if not _positive_int(line.get("box_count")):
                    return "散箱数量（box_count）必须是正整数。"
                return None
            if not _positive_int(line.get("boxes_per_pallet")):
                return "每托箱数（boxes_per_pallet）必须是正整数。"
            if not _positive_int(line.get("pallet_count")):
                return "托数（pallet_count）必须是正整数。"
            return None

        messages = _typed_line_issues(lines, check)
        return "请修正以下商品明细：" + "；".join(messages) if messages else None

    return validator


_valid_inbound_sku_quantities = _valid_sku_line_quantities("sku_lines")
_valid_outbound_sku_quantities = _valid_sku_line_quantities("sku_lines")


def _valid_outbound_sku_lines(context: dict, collected_fields: dict, db: DBSession) -> str | None:
    """uchoice_outbound_request: reject missing or uncataloged line SKUs (Sev 2).

    Reuses the same shared primitive Codex's Phase 2 validators use, applied
    here for Phase 1 -- a fabricated/missing sku_code on an outbound line
    (e.g. {"box_count": 30} with no product identified at all) must not
    reach confirmation as "?", the same way a bad destination_address_id
    must not reach it either.
    """
    del context
    issues = validate_sku_lines(
        collected_fields.get("sku_lines"),
        field_name="sku_lines",
        db=db,
    )
    return format_validation_issues(issues)


def _valid_destination_address_required(context: dict, collected_fields: dict, db: DBSession) -> str | None:
    """
    uchoice_outbound_request: destination_address_id must be a real
    uchoice_address row — the AI is instructed to only fill it in from the
    injected address candidate list (a fuzzy match against real UUIDs), but
    when nothing in that list matches what the customer described, it has
    been observed live to fall back to writing the customer's free-text
    company/address description into destination_address_id instead of
    leaving it unset. That string then reaches a raw
    `WHERE address_id = <value>::UUID` query downstream and crashes with a
    DB-level type error, not a clean message — this is the same class of bug
    as the loose-line/pallet-bucket issues fixed earlier: an AI field
    extraction that's supposed to be constrained to a known set of values
    needs a deterministic backstop, not just a prompt instruction.

    Also rejects an address whose OWN uchoice_address.warehouse_code (which
    source warehouse's outbound requests this address is a legitimate
    candidate for) doesn't match this request's collected warehouse_code.
    Observed live: two addresses can share the identical physical addr and
    differ only by which warehouse's outbound flow they're meant for (e.g.
    "DE Warehouse", warehouse_code=JFK, a real JFK->DE inter-warehouse
    transfer vs. "DE仓库自提留存", warehouse_code=DE, a same-address
    self-pickup entry that does NOT credit any warehouse's storage) --
    nothing previously stopped the wrong one from reaching confirmation for
    a JFK-sourced request. A null addr.warehouse_code (e.g. "散客"/walk-in)
    is warehouse-agnostic and always allowed.
    """
    dest_id = collected_fields.get("destination_address_id")
    if not dest_id:
        return None

    not_found_message = (
        "未能识别这个送货地址——它还没有被收录在地址库中，请提供完整的公司名、地址"
        "（门牌号+街道+城市+州+邮编）以及计费类型，联系管理员添加后再重新提交。"
    )

    import uuid
    try:
        uuid.UUID(str(dest_id))
    except (ValueError, TypeError):
        # Not even UUID-shaped — never send this to the DB. A raw
        # `::UUID` cast on a non-UUID string fails at the driver level and
        # leaves the SQLAlchemy session in a failed-transaction state for
        # the rest of this request, which is worse than the original crash.
        return not_found_message

    from models.uchoice import UchoiceAddress
    addr = db.query(UchoiceAddress).filter_by(address_id=dest_id).first()
    if addr is None:
        return not_found_message

    warehouse_code = collected_fields.get("warehouse_code")
    if warehouse_code and addr.warehouse_code and addr.warehouse_code != warehouse_code:
        label = addr.company_name or addr.addr
        return (
            f"送货地址「{label}」不是 {warehouse_code} 仓出库申请的可选目的地"
            f"（该地址仅适用于 {addr.warehouse_code} 仓出库），请重新确认目的地或联系管理员。"
        )
    return None


PRE_CONFIRM_VALIDATORS = {
    "role_change": _compose(
        _valid_role_change_target_and_role,
        _last_admin_protection,
    ),
    "uchoice_outbound_request": _compose(
        _valid_outbound_sku_lines,
        _valid_outbound_sku_quantities,
        _valid_destination_address_required,
    ),
    "uchoice_inbound_request": _compose(
        _valid_inbound_sku_lines,
        _valid_inbound_sku_quantities,
    ),
    "adjust_storage": _valid_adjust_storage_lines,
    "move_storage": _compose(
        _valid_move_storage_lines,
        _move_storage_stock_available,
    ),
    "recount_storage": _valid_recount_storage_lines,
    "confirm_outbound_completion": _compose(
        _valid_outbound_completion_skus,
        _loose_outbound_pick_required,
    ),
    "confirm_inbound_completion": _compose(
        _valid_inbound_completion_skus,
        _loose_inbound_restatement_required,
    ),
}


def run(service_type_name: str, context: dict, collected_fields: dict, db: DBSession) -> str | None:
    """Returns an error message if the request should be blocked, else None."""
    validator = PRE_CONFIRM_VALIDATORS.get(service_type_name)
    if validator is None:
        return None
    return validator(context, collected_fields, db)
