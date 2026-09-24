"""
Batch completion confirmation -- confirm_inbound_completion_batch /
confirm_outbound_completion_batch, Kefu only. Full design and the review
findings that shaped it: docs/reviews/active/2026-09-batch-completion-
confirmation/plan.md.

Three layers, deliberately separated:
- The AI only proposes a *selection* ({"select_all", "indices", "serials",
  "exclude_indices", "exclude_serials"}); it never writes reference_serials.
- Code (resolve_selection / prepare_batch below) decides membership against
  a stored, numbered snapshot the user actually saw, and drops anything that
  isn't a real, eligible, batch-executable candidate.
- The user approves the rendered summary before anything mutates.

Pick allocation is SIMULATED, never persisted as execution input: a partial
pick turns one pallet into a new smaller bucket (core.uchoice_storage.
apply_loose_pick), so a pick computed for request ② can depend on request
① having run first. simulate_batch_allocation mirrors exactly what
handlers/uchoice/storage_txns.py's Kefu box-level path does, and is re-run
under lock at execution time (handlers/uchoice/complete_batch.py) and
compared against the previewed picks before anything changes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import SimpleNamespace

from sqlalchemy.orm import Session as DBSession

MAX_BATCH_SIZE = 9
_CIRCLED = "①②③④⑤⑥⑦⑧⑨"

BATCH_SERVICE_BY_SINGLE = {
    "confirm_inbound_completion": "confirm_inbound_completion_batch",
    "confirm_outbound_completion": "confirm_outbound_completion_batch",
}
SINGLE_SERVICE_BY_BATCH = {v: k for k, v in BATCH_SERVICE_BY_SINGLE.items()}
DIRECTION_BY_SERVICE = {
    "confirm_inbound_completion": "inbound",
    "confirm_outbound_completion": "outbound",
    "confirm_inbound_completion_batch": "inbound",
    "confirm_outbound_completion_batch": "outbound",
}
CANDIDATE_KEY_BY_DIRECTION = {
    "inbound": "pending_inbound_requests",
    "outbound": "pending_outbound_requests",
}
DIRECTION_LABELS = {"inbound": "入库", "outbound": "出库"}

AMBIGUOUS = "ambiguous"

# The ONLY replies that execute a whole pending batch (besides a pure number
# reply, parsed separately). Matched in code, never inferred by the AI: a
# reply like "确认，但第三笔少发两箱" is not on this list, so it can never run
# the batch at original quantities (audit round 4, P1).
AFFIRMATIVE_REPLIES = frozenset({
    "确认", "确定", "确认全部", "全部确认", "全部提交", "确认提交", "提交",
    "好", "好的", "可以", "是", "是的", "对", "没问题", "ok", "okay", "yes", "y",
})
_TRAILING_PUNCTUATION = " \t\r\n。！!.~～"


def is_affirmative(text: str) -> bool:
    return (text or "").strip().rstrip(_TRAILING_PUNCTUATION).strip().lower() in AFFIRMATIVE_REPLIES


# Exact replies to a batch's "请问要确认哪几笔" list that mean every listed item.
SELECT_ALL_REPLIES = frozenset({"全部", "全部确认", "都确认", "全部都确认", "所有", "所有都确认", "都要"})

# Quantity-restating fields the batch deliberately never accepts -- a
# batch always completes every request at its original quantities.
QUANTITY_FIELDS = ("fulfillment_lines", "received_lines", "destination_packing_lines")


def is_batch_service(name: str | None) -> bool:
    return name in SINGLE_SERVICE_BY_BATCH


def circled(index: int) -> str:
    """1-based index -> ①..⑨."""
    return _CIRCLED[index - 1]


# ── Deterministic number-reply parsing ───────────────────────────────────────

_CIRCLED_VALUE = {c: i for i, c in enumerate(_CIRCLED, start=1)}
_FILLER_WORDS = ("部分确认", "确认", "都", "号", "第", "个", "条", "笔")
_SEPARATORS = re.compile(r"[\s,，、;；.。和及与跟/+]+")


def parse_partial_reply(text: str, n: int):
    """
    Parses a reply made only of item numbers ("13", "1 3", "①③", "1、3",
    "部分确认 1 3") against a numbered list of n items (n <= 9, so every digit
    is exactly one item).

    Returns:
      - a sorted list of distinct 1-based indices, when the reply is purely
        numbers that all exist;
      - AMBIGUOUS when it is a number reply that can't be read safely (an
        index outside 1..n, a 0, a repeat) or a bare "部分确认" with no numbers;
      - None when it isn't a number reply at all (anything with other text,
        including "确认" alone) -- the caller hands it to the normal AI path.
    """
    if not text:
        return None
    stripped = text.strip()
    if stripped == "部分确认":
        return AMBIGUOUS

    body = stripped
    for word in _FILLER_WORDS:
        body = body.replace(word, "")
    body = _SEPARATORS.sub("", body)
    if not body:
        return None

    indices = []
    for ch in body:
        if ch in _CIRCLED_VALUE:
            indices.append(_CIRCLED_VALUE[ch])
        elif ch.isdigit() and ch.isascii():
            indices.append(int(ch))
        else:
            return None  # mixed text -- not a pure number reply

    if any(i < 1 or i > n for i in indices) or len(set(indices)) != len(indices):
        return AMBIGUOUS
    return sorted(indices)


# ── Selection resolution ─────────────────────────────────────────────────────

def snapshot_serials(candidates: list[dict]) -> list[str]:
    """The numbered list: oldest first (pending_request_candidates already
    orders by created_at), capped at MAX_BATCH_SIZE."""
    return [c["serial_number"] for c in candidates if c.get("serial_number")][:MAX_BATCH_SIZE]


def _match_serial(token, eligible: list[str]) -> str | None:
    """Exact serial, or a unique suffix ("086" -> REQ-20260922-000086)."""
    if not isinstance(token, str) or not token.strip():
        return None
    token = token.strip()
    if token in eligible:
        return token
    matches = [s for s in eligible if s.endswith(token)]
    return matches[0] if len(matches) == 1 else None


def _as_int_list(value) -> list[int]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, int) and not isinstance(v, bool)]


def resolve_selection(selection, snapshot: list[str], eligible: list[str]) -> tuple[list[str], list[str]]:
    """
    Turns the AI's (or the number parser's) selection into serials, never
    trusting it: indices resolve against the stored snapshot the user saw,
    serials must be currently eligible, and the result is ordered by the
    eligible list (oldest first) and capped at MAX_BATCH_SIZE.

    Returns (serials, notes) -- notes explain anything that was asked for
    but couldn't be included.
    """
    selection = selection if isinstance(selection, dict) else {}
    notes: list[str] = []
    chosen: list[str] = []

    if selection.get("select_all") is True:
        chosen += snapshot
    for i in _as_int_list(selection.get("indices")):
        if 1 <= i <= len(snapshot):
            chosen.append(snapshot[i - 1])
        else:
            notes.append(f"编号 {i} 不存在")
    for token in selection.get("serials") or []:
        matched = _match_serial(token, eligible)
        if matched:
            chosen.append(matched)
        else:
            notes.append(f"{token} 不在可确认的待处理申请中")

    excluded = set()
    for i in _as_int_list(selection.get("exclude_indices")):
        if 1 <= i <= len(snapshot):
            excluded.add(snapshot[i - 1])
    for token in selection.get("exclude_serials") or []:
        matched = _match_serial(token, eligible)
        if matched:
            excluded.add(matched)

    wanted = {s for s in chosen if s not in excluded}
    for s in sorted(wanted - set(eligible)):
        notes.append(f"{s} 已不在可确认的待处理申请中")
    ordered = [s for s in eligible if s in wanted]
    if len(ordered) > MAX_BATCH_SIZE:
        notes.append(f"一次最多确认 {MAX_BATCH_SIZE} 笔，其余 {len(ordered) - MAX_BATCH_SIZE} 笔请稍后再次确认")
        ordered = ordered[:MAX_BATCH_SIZE]
    return ordered, notes


# ── Targets ──────────────────────────────────────────────────────────────────

@dataclass
class BatchTarget:
    serial: str
    log: object
    direction: str | None
    original_fields: dict
    warehouse_code: str | None
    destination_warehouse_code: str | None = None
    destination_label: str | None = None

    def sku_lines(self) -> list[dict]:
        return [l for l in (self.original_fields.get("sku_lines") or []) if isinstance(l, dict)]


def load_target(db: DBSession, serial: str) -> BatchTarget | None:
    from models.request_log import RequestLog
    from models.service import ServiceType
    from models.uchoice import UchoiceAddress
    from core.uchoice_context import get_original_fields, format_address_label

    log = db.query(RequestLog).filter_by(serial_number=serial).first()
    if log is None:
        return None
    service = db.query(ServiceType).filter_by(service_type_id=log.service_type_id).first()
    direction = {"uchoice_inbound_request": "inbound", "uchoice_outbound_request": "outbound"}.get(
        service.name if service else None
    )
    original_fields = get_original_fields(db, log) or {}
    target = BatchTarget(
        serial=serial, log=log, direction=direction, original_fields=original_fields,
        warehouse_code=original_fields.get("warehouse_code"),
    )
    destination_address_id = original_fields.get("destination_address_id")
    if destination_address_id:
        addr = db.query(UchoiceAddress).filter_by(address_id=destination_address_id).first()
        target.destination_label = format_address_label(addr)
        if addr is not None:
            target.destination_warehouse_code = addr.destination_warehouse_code
    return target


def _is_positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def ineligibility_reason(target: BatchTarget, direction: str) -> str | None:
    """
    Requests a batch can't complete at their original quantities without
    packing input the batch deliberately doesn't collect (review finding 3):
    - loose inbound: ApplyInboundStorageHandler requires an explicit
      received_lines restatement for box_count lines;
    - loose internal transfer: the Kefu outbound path requires
      destination_packing_lines for loose/picked lines.
    Both must be confirmed individually.
    """
    if target.log.status != "processing":
        return f"当前状态为「{target.log.status}」，无法处理"
    if target.direction != direction:
        return f"不是{DIRECTION_LABELS[direction]}申请"
    lines = target.sku_lines()
    if not lines:
        return "申请缺少商品明细，请单独确认"
    loose = [l for l in lines if "box_count" in l]
    if direction == "inbound" and loose:
        return "含散箱入库，需说明装托方式，请单独确认"
    if direction == "outbound" and loose and target.destination_warehouse_code:
        return "含散箱的内部调仓，需说明目的仓装托方式，请单独确认"
    for line in lines:
        if not line.get("sku_code"):
            return "申请明细不完整，请单独确认"
        if "box_count" in line:
            if not _is_positive_int(line["box_count"]):
                return "申请明细不完整，请单独确认"
        elif not (_is_positive_int(line.get("boxes_per_pallet")) and _is_positive_int(line.get("pallet_count"))):
            return "申请明细不完整，请单独确认"
    return None


def required_boxes(target: BatchTarget) -> dict[str, int]:
    """Per-SKU box totals, computed exactly like
    ApplyOutboundStorageHandler._handle_kefu_box_level._line_box_count."""
    required: dict[str, int] = {}
    for line in target.sku_lines():
        sku = line["sku_code"]
        count = line["box_count"] if "box_count" in line else line["boxes_per_pallet"] * line["pallet_count"]
        required[sku] = required.get(sku, 0) + count
    return required


def destination_packing(target: BatchTarget) -> list[dict]:
    """For a (whole-pallet) internal transfer: the original lines' own
    packing, credited at the destination exactly as the handler does with
    destination_packing_lines (storage_txns.py:314-320)."""
    return [
        {"sku_code": l["sku_code"], "boxes_per_pallet": l["boxes_per_pallet"], "pallet_count": l["pallet_count"]}
        for l in target.sku_lines()
    ]


def storage_scopes(targets: list[BatchTarget]) -> list[tuple[str, str]]:
    """Every (warehouse, SKU) the batch can touch: origins, plus transfer
    destinations -- the complete union, acquired in one sorted call."""
    scopes = set()
    for t in targets:
        for sku in {l["sku_code"] for l in t.sku_lines() if l.get("sku_code")}:
            scopes.add((t.warehouse_code, sku))
            if t.destination_warehouse_code:
                scopes.add((t.destination_warehouse_code, sku))
    return sorted(s for s in scopes if s[0] and s[1])


# ── Allocation simulation ────────────────────────────────────────────────────

@dataclass
class SimResult:
    picks: dict[str, dict[str, list[dict]]] = field(default_factory=dict)   # serial -> sku -> picks
    shortages: dict[str, list[dict]] = field(default_factory=dict)          # serial -> shortage rows
    final_buckets: dict[tuple[str, str], dict[int, int]] = field(default_factory=dict)


def load_buckets(db: DBSession, scopes: list[tuple[str, str]]) -> dict[tuple[str, str], dict[int, int]]:
    """Current pallet buckets for the given scopes. populate_existing so a
    read under lock never returns stale identity-mapped rows."""
    from models.uchoice import UchoiceStorage

    buckets: dict[tuple[str, str], dict[int, int]] = {scope: {} for scope in scopes}
    for warehouse_code, sku_code in scopes:
        rows = (
            db.query(UchoiceStorage)
            .filter_by(warehouse_code=warehouse_code, sku_code=sku_code)
            .populate_existing()
            .all()
        )
        for r in rows:
            buckets[(warehouse_code, sku_code)][r.boxes_per_pallet] = r.pallet_count
    return buckets


def _apply_source_pick(bucket: dict[int, int], source_bpp: int, box_count: int) -> None:
    """Mirrors core.uchoice_storage.apply_loose_pick with
    destination_warehouse_code=None: whole pallets decrement; a remainder
    consumes one more pallet and leaves a new, smaller bucket behind."""
    full, remainder = divmod(box_count, source_bpp)
    bucket[source_bpp] = bucket.get(source_bpp, 0) - full
    if remainder:
        bucket[source_bpp] = bucket.get(source_bpp, 0) - 1
        leftover = source_bpp - remainder
        bucket[leftover] = bucket.get(leftover, 0) + 1


def simulate_allocation(buckets: dict[tuple[str, str], dict[int, int]], targets: list[BatchTarget]) -> SimResult:
    """
    Pure: walks outbound targets in the given order against an in-memory
    copy of `buckets`, allocating each SKU with the same allocate_box_picks
    the executor uses. A target that comes up short on any SKU is recorded
    in shortages and applies nothing. Destination credit for an internal
    transfer comes from destination_packing(), separately from the source
    picks -- the same two mutations the Kefu handler applies (round-2
    finding 1).
    """
    from core.uchoice_storage import allocate_box_picks

    state = {scope: dict(b) for scope, b in buckets.items()}
    result = SimResult()
    for t in targets:
        planned: dict[str, list[dict]] = {}
        shortage = []
        for sku, needed in sorted(required_boxes(t).items()):
            bucket = state.setdefault((t.warehouse_code, sku), {})
            rows = [SimpleNamespace(boxes_per_pallet=bpp, pallet_count=count) for bpp, count in bucket.items()]
            picks = allocate_box_picks(rows, needed)
            if picks is None:
                available = sum(max(0, c) * bpp for bpp, c in bucket.items())
                shortage.append({"sku_code": sku, "requested_boxes": needed, "available_boxes": available})
            else:
                planned[sku] = picks
        if shortage:
            result.shortages[t.serial] = shortage
            continue
        for sku, picks in planned.items():
            bucket = state[(t.warehouse_code, sku)]
            for p in picks:
                _apply_source_pick(bucket, p["source_boxes_per_pallet"], p["box_count"])
        if t.destination_warehouse_code:
            for line in destination_packing(t):
                bucket = state.setdefault((t.destination_warehouse_code, line["sku_code"]), {})
                bucket[line["boxes_per_pallet"]] = bucket.get(line["boxes_per_pallet"], 0) + line["pallet_count"]
        result.picks[t.serial] = planned
    result.final_buckets = {scope: {bpp: c for bpp, c in b.items() if c} for scope, b in state.items()}
    return result


# ── Preparing a batch for display ────────────────────────────────────────────

@dataclass
class PreparedBatch:
    serials: list[str]
    targets: dict[str, BatchTarget]
    preview_picks: dict[str, dict[str, list[dict]]]
    notes: list[str]


def _shortage_note(db: DBSession, serial: str, shortage: list[dict]) -> str:
    from core.uchoice_context import sku_label_map
    labels = sku_label_map(db)
    parts = [
        f'{labels.get(s["sku_code"], s["sku_code"])} 需 {s["requested_boxes"]} 箱，现有 {s["available_boxes"]} 箱'
        for s in shortage
    ]
    return f"{serial}：库存不足（{'；'.join(parts)}），请单独处理"


def prepare_batch(db: DBSession, serials: list[str], direction: str, notes: list[str] | None = None) -> PreparedBatch:
    """
    Drops everything a batch can't execute (not found, wrong status or
    direction, needs packing input, insufficient stock at its position in
    the batch) and simulates picks for what remains, in display order.
    Shortage-dropping re-simulates, since removing one request frees stock
    for the ones after it.
    """
    notes = list(notes or [])
    targets: dict[str, BatchTarget] = {}
    kept: list[str] = []
    for serial in serials:
        target = load_target(db, serial)
        if target is None:
            notes.append(f"{serial}：未找到该申请")
            continue
        reason = ineligibility_reason(target, direction)
        if reason:
            notes.append(f"{serial}：{reason}")
            continue
        targets[serial] = target
        kept.append(serial)

    preview: dict[str, dict[str, list[dict]]] = {}
    if direction == "outbound" and kept:
        buckets = load_buckets(db, storage_scopes([targets[s] for s in kept]))
        while True:
            sim = simulate_allocation(buckets, [targets[s] for s in kept])
            if not sim.shortages:
                preview = sim.picks
                break
            for serial, shortage in sim.shortages.items():
                notes.append(_shortage_note(db, serial, shortage))
            kept = [s for s in kept if s not in sim.shortages]
            if not kept:
                break
    return PreparedBatch(serials=kept, targets=targets, preview_picks=preview, notes=notes)


def picks_match(expected: dict | None, actual: dict | None) -> bool:
    """Order-insensitive comparison of one target's {sku: picks}."""
    def norm(picks_by_sku):
        return {
            sku: sorted((p["source_boxes_per_pallet"], p["box_count"]) for p in picks)
            for sku, picks in (picks_by_sku or {}).items()
        }
    return norm(expected) == norm(actual)
