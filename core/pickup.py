"""
Customer pickup (客户自提) for outbound requests.
docs/ai-collaboration/2026-11-customer-pickup/plan.md (rev 11, signed).

Three kinds of self-pickup destination exist in the address book, told apart
by uchoice_address.pickup_kind (V42), never by label text:

  retention  仓库自提留存 -- the recipient is Trans World itself; the goods are
             already in our warehouse. Stock is deducted as usual. One row per
             warehouse.
  customer   客户自提 -- a named outside customer collects at the warehouse.
             ONE general row (warehouse_code NULL = any source warehouse).
  legacy     散客 -- retired; kept so past requests still render. Never
             returned by the resolver.

The LLM never picks one of these rows. It reports intent through two fields
inside `extracted_fields` -- `pickup_type` (customer / retention / none; absent
= no change this turn) and `pickup_customer_name` -- and CODE resolves the
destination row from (pickup_type, source warehouse).

This module is the single chokepoint shared by both channels:
  * sanitize_extracted()  -- before persistence; drops invalid values and any
                             AI-written pickup-row destination ID
  * apply_turn()          -- after the merge, before address matching; the
                             transition step (clears stale fields, resolves
                             the row), the vocabulary backstop
  * intent_this_turn()    -- the effective mode, used by Smart Robot's
                             address-pivot gate
  * validate_combination()-- final consistency check (pre-confirm validator)
  * display helpers       -- the one place pickup wording is built
"""
import re
import uuid

from sqlalchemy.orm import Session as DBSession

OUTBOUND_SERVICE = "uchoice_outbound_request"

TYPE_KEY = "pickup_type"
NAME_KEY = "pickup_customer_name"
# Internal (an "_" key, so the AI can never write it): stamped on every
# outbound request created after V42. A request WITHOUT it is grandfathered --
# judged by the old rules, no combination check -- so drafts, pending
# confirmations and processing requests that predate the migration keep working.
MARKER_KEY = "_pickup_v"
MARKER_VALUE = 1

TYPE_CUSTOMER = "customer"
TYPE_RETENTION = "retention"
TYPE_NONE = "none"
PICKUP_TYPES = (TYPE_CUSTOMER, TYPE_RETENTION)
ALL_TYPES = (TYPE_CUSTOMER, TYPE_RETENTION, TYPE_NONE)

KIND_CUSTOMER = "customer"
KIND_RETENTION = "retention"
KIND_LEGACY = "legacy"

NAME_MAX_LEN = 40
LIST_NAME_MAX_LEN = 20

ASK_WHICH = (
    "这笔出库是自提吗？请说明是「客户自提」（有客户到仓库取货，请告知取货人姓名）"
    "还是「仓库自提留存」（货物留给公司自己，无需移动）。如果是送货，请告知送货地址。"
)

# ── Vocabulary (backstop only; the AI decides intent, code catches disagreement)

_VOCAB = (
    r"自提|自取|自己(?:来)?(?:提|取|拿)|来(?:提|取|拿)货?|到仓(?:提|取|库提|库取)|提货|取货"
    r"|pick\s*-?\s*up|will\s*-?\s*call"
)
_VOCAB_RE = re.compile(_VOCAB, re.IGNORECASE)
_NEGATED_RE = re.compile(r"(?:不是|不用|不要|无需|无须|非|不)\s*(?:" + _VOCAB + ")", re.IGNORECASE)


def vocabulary_flags(message: str, address_texts=()) -> tuple[bool, bool]:
    """(positive, negated): un-negated pickup wording present / negated wording
    present. Text matching a known address's company or address is ignored, so
    a company literally named with 自提 never triggers."""
    text = message or ""
    for chunk in address_texts or ():
        if chunk and isinstance(chunk, str) and len(chunk) >= 2:
            text = text.replace(chunk, " ")
    negated = bool(_NEGATED_RE.search(text))
    positive = bool(_VOCAB_RE.search(_NEGATED_RE.sub(" ", text)))
    return positive, negated


# ── Small helpers ─────────────────────────────────────────────────────────────

def is_outbound(service_name) -> bool:
    return service_name == OUTBOUND_SERVICE


def new_request_fields(service_name) -> dict:
    """Initial collected_fields for a NEW outbound request (the grandfather marker)."""
    return {MARKER_KEY: MARKER_VALUE} if is_outbound(service_name) else {}


def has_marker(fields) -> bool:
    return (fields or {}).get(MARKER_KEY) == MARKER_VALUE


def clean_name(raw) -> str | None:
    """Pickup person's name: one line, whitespace-normalized, bounded."""
    if not isinstance(raw, str):
        return None
    text = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", raw).split())
    text = text[:NAME_MAX_LEN].strip()
    return text or None


def _as_uuid(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def kind_of(db: DBSession, address_id) -> str | None:
    """pickup_kind of an address row ('customer'/'retention'/'legacy') or None."""
    parsed = _as_uuid(address_id)
    if parsed is None:
        return None
    from models.uchoice import UchoiceAddress
    row = db.query(UchoiceAddress.pickup_kind).filter(UchoiceAddress.address_id == parsed).first()
    return row[0] if row else None


def resolve_destination(db: DBSession, pickup_type, warehouse_code) -> str | None:
    """The address_id for a pickup type, chosen by code. customer -> the one
    general row; retention -> the source warehouse's own row. Never legacy."""
    from models.uchoice import UchoiceAddress
    if pickup_type == TYPE_CUSTOMER:
        rows = db.query(UchoiceAddress).filter(UchoiceAddress.pickup_kind == KIND_CUSTOMER).all()
    elif pickup_type == TYPE_RETENTION and warehouse_code:
        rows = db.query(UchoiceAddress).filter(
            UchoiceAddress.pickup_kind == KIND_RETENTION,
            UchoiceAddress.warehouse_code == warehouse_code,
        ).all()
    else:
        return None
    return str(rows[0].address_id) if len(rows) == 1 else None


def default_warehouse(context: dict) -> str | None:
    """Same rule as the channels' own warehouse default: an unscoped caller
    defaults to JFK, a caller with exactly one assigned warehouse uses it,
    several assigned and nothing stated gives no default (they are asked)."""
    codes = (context or {}).get("warehouse_codes")
    if codes is None:
        return "JFK"
    if len(codes) == 1:
        return list(codes)[0]
    return None


# ── 1. Sanitizer (before persistence, both channels) ──────────────────────────

def sanitize_extracted(extracted: dict, db: DBSession) -> dict:
    """Outbound requests only. Drops an invalid pickup_type, cleans the name,
    and removes any AI-written destination_address_id that points at a pickup
    row -- only the resolver may write one."""
    out = dict(extracted or {})
    if TYPE_KEY in out:
        value = out[TYPE_KEY]
        value = value.strip().lower() if isinstance(value, str) else None
        if value in ALL_TYPES:
            out[TYPE_KEY] = value
        else:
            out.pop(TYPE_KEY)
    if NAME_KEY in out:
        name = clean_name(out[NAME_KEY])
        if name:
            out[NAME_KEY] = name
        else:
            out.pop(NAME_KEY)
    dest = out.get("destination_address_id")
    if dest is not None and kind_of(db, dest) is not None:
        out.pop("destination_address_id")
    return out


# ── 2. Intent and vocabulary backstop ─────────────────────────────────────────

def persisted_mode(fields) -> str:
    """The pickup mode a request is currently in: customer / retention / none."""
    value = (fields or {}).get(TYPE_KEY)
    return value if value in PICKUP_TYPES else TYPE_NONE


def intent_this_turn(ai_type, persisted: str, message: str, address_texts=()) -> str:
    """The EFFECTIVE mode after this turn: customer / retention / none, or
    'ask' when the AI's classification and the user's own words disagree.

    Only a NEWLY stated intent is checked (a name-only follow-up carries no
    pickup_type and no pickup wording, and must keep the persisted mode)."""
    positive, negated = vocabulary_flags(message, address_texts)
    if ai_type in PICKUP_TYPES:
        if ai_type != persisted and not positive:
            return "ask"            # AI says pickup, the words don't: never a silent $0
        return ai_type
    if ai_type == TYPE_NONE:
        if persisted == TYPE_NONE and positive:
            return "ask"            # AI says delivery, the words say pickup
        return TYPE_NONE
    # AI said nothing about pickup this turn.
    if persisted == TYPE_NONE:
        return "ask" if positive else TYPE_NONE
    if negated and not positive:
        return "ask"                # "不是自提" but the AI kept the pickup
    return persisted


def _address_texts(context: dict) -> list[str]:
    texts = []
    for cand in ((context or {}).get("uchoice_candidates") or {}).get("addresses") or []:
        for key in ("company_name", "addr"):
            if cand.get(key):
                texts.append(cand[key])
    return texts


def intent_for_pivot(context: dict, ai_response, persisted_fields) -> str:
    """Smart Robot's address-pivot gate: the effective mode BEFORE the pivot
    can cancel anything. customer / retention / ask suppress the pivot."""
    extracted = getattr(ai_response, "extracted_fields", None) or {}
    ai_type = extracted.get(TYPE_KEY) if isinstance(extracted, dict) else None
    if isinstance(ai_type, str):
        ai_type = ai_type.strip().lower()
    if ai_type not in ALL_TYPES:
        ai_type = None
    return intent_this_turn(
        ai_type, persisted_mode(persisted_fields), (context or {}).get("content") or "", _address_texts(context),
    )


# ── 3. The transition step (after the merge, before address matching) ─────────

def apply_turn(db: DBSession, context: dict, previous: dict, merged: dict, extracted: dict) -> tuple[dict, str | None]:
    """Both channels, outbound only. Returns (fields, ask_text).

    * pickup_type customer/retention this turn: drop any stale destination,
      resolve the pickup row by code. Pickup wins over a named address.
    * pickup_type none this turn: drop the pickup type, name and any pickup-row
      destination so normal address matching runs on this same turn.
    * absent: no change, but a persisted pickup mode still gets its row
      resolved (e.g. retention once the warehouse is known).
    Never relies on the AI emitting null. When the AI and the user's words
    disagree it returns ask_text and leaves the pickup fields as they were."""
    fields = dict(merged or {})
    ai_type = (extracted or {}).get(TYPE_KEY)
    persisted = persisted_mode(previous)

    mode = intent_this_turn(ai_type, persisted, (context or {}).get("content") or "", _address_texts(context))
    if mode == "ask":
        for key in (TYPE_KEY, NAME_KEY):
            if key in (previous or {}):
                fields[key] = previous[key]
            else:
                fields.pop(key, None)
        return fields, ASK_WHICH

    if ai_type in PICKUP_TYPES:
        fields[TYPE_KEY] = ai_type
        fields.pop("destination_address_id", None)
        fields[MARKER_KEY] = MARKER_VALUE        # judged strictly from here on
    elif ai_type == TYPE_NONE:
        fields.pop(TYPE_KEY, None)
        fields.pop(NAME_KEY, None)
        if kind_of(db, fields.get("destination_address_id")) is not None:
            fields.pop("destination_address_id", None)

    current = fields.get(TYPE_KEY)
    if current == TYPE_RETENTION:
        fields.pop(NAME_KEY, None)               # the recipient is us; no person
    if current in PICKUP_TYPES:
        warehouse = fields.get("warehouse_code") or default_warehouse(context)
        resolved = resolve_destination(db, current, warehouse)
        if resolved:
            fields["destination_address_id"] = resolved
    return fields, None


# ── 4. Final-combination validator (pre-confirm) ──────────────────────────────

def validate_combination(fields: dict, db: DBSession) -> str | None:
    """(pickup_type, destination row kind, warehouse, name) must be consistent.
    Fails closed for requests created after V42; a request without the marker
    is grandfathered and judged by the old rules."""
    fields = fields or {}
    if not has_marker(fields):
        return None
    mode = persisted_mode(fields)
    dest = fields.get("destination_address_id")
    dest_kind = kind_of(db, dest)
    name = clean_name(fields.get(NAME_KEY))

    if mode == TYPE_CUSTOMER:
        if dest_kind != KIND_CUSTOMER:
            return "出库目的地与「客户自提」不一致，请重新说明是自提还是送货。"
        if not name:
            return "客户自提需要登记取货人，请提供取货人姓名（如果是散客，请回复「散客」）。"
        return None
    if mode == TYPE_RETENTION:
        if name:
            return ("您提供了取货人姓名，但选择了「仓库自提留存」（货物留给公司自己）。"
                    "请问是「客户自提」（有客户到仓库取货）还是「仓库自提留存」？")
        from models.uchoice import UchoiceAddress
        parsed = _as_uuid(dest)
        row = db.query(UchoiceAddress).filter(UchoiceAddress.address_id == parsed).first() if parsed else None
        if dest_kind != KIND_RETENTION or row is None or row.warehouse_code != fields.get("warehouse_code"):
            return "出库目的地与「仓库自提留存」不一致，请重新说明是自提还是送货。"
        return None
    # No pickup mode: an ordinary delivery.
    if dest_kind is not None:
        return "出库目的地与申请类型不一致，请重新说明是自提还是送货。"
    if name:
        return "您提供了取货人姓名，请问这笔出库是「客户自提」吗？如果是送货，请告知送货地址。"
    return None


# ── 5. Display (the one place pickup wording is built) ────────────────────────

def _short(name: str) -> str:
    return name if len(name) <= LIST_NAME_MAX_LEN else name[: LIST_NAME_MAX_LEN - 1] + "…"


def list_label(fields: dict) -> str | None:
    """The `→` line of a list / the destination of a confirmation, or None when
    the request is an ordinary delivery or predates this feature."""
    fields = fields or {}
    mode = persisted_mode(fields)
    if mode == TYPE_CUSTOMER:
        name = clean_name(fields.get(NAME_KEY))
        return f"【自提】客户自提：{_short(name)}（无需送货）" if name else "【自提】客户自提（无需送货）"
    if mode == TYPE_RETENTION:
        return "【自提】仓库自提留存（货不出仓）"
    return None


def confirmation_destination(fields: dict) -> str | None:
    """`目的地：...` text for a single confirmation."""
    fields = fields or {}
    mode = persisted_mode(fields)
    if mode == TYPE_CUSTOMER:
        name = clean_name(fields.get(NAME_KEY))
        return f"客户自提 — 取货人：{_short(name)}" if name else "客户自提"
    if mode == TYPE_RETENTION:
        return "仓库自提留存"
    return None


def warning_line(fields: dict) -> str | None:
    """One extra line on the single-request completion confirmation."""
    mode = persisted_mode(fields)
    if mode == TYPE_CUSTOMER:
        return "⚠️ 自提：无需送货，货物由客户到仓提取；如非自提请先联系客服"
    if mode == TYPE_RETENTION:
        return "⚠️ 仓库自提留存：货物留在仓库，无需送货；如非留存请先联系客服"
    return None


def invoice_destination(fields: dict) -> str | None:
    """Destination cell on the invoice detail sheet (D14)."""
    fields = fields or {}
    if persisted_mode(fields) == TYPE_CUSTOMER:
        name = clean_name(fields.get(NAME_KEY))
        return f"客户自提（{name}）" if name else "客户自提"
    return None
