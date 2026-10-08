"""
Container number (柜号) and container unpacking fee (拆柜费) for inbound
requests -- shared by Kefu (core/kefu_turn_apply.py) and Smart Robot
(core/workflow_engine.py). Plan: docs/ai-collaboration/2026-10-unpacking-fee/
plan.md (rev 5).

- An inbound request always records a 柜号, or 无.
- At receipt, a request with a 柜号 needs the warehouse to give the fee; one
  without gets $0 unless the warehouse enters a fee itself.
- Values are read from the user's own message, never trusted from the AI's
  reading alone (R4): the AI's value is accepted only when it matches what
  was typed, and the scan is field-aware so container digits, request IDs
  and quantities can never validate a fee (R6).
- A non-standard 柜号 or a fee over the cap is questioned once
  (_pending_value_check) and accepted if confirmed (D3, Q1). The answer is
  handled before the AI and never confirms the request itself (R1).

Internal collected_fields keys (the AI can never write "_" keys):
  _pending_value_check   {"field": ..., "value": ...} -- an open double-check
  _value_accepted        {field: value} -- confirmed non-standard values
  _unpacking_fee_invalid True after an invalid fee, until a valid one (R3)
  _asked_field           the field the bot's last question asked for
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

NONE_VALUE = "无"
FEE_CAP = Decimal("10000")

PENDING_KEY = "_pending_value_check"
ACCEPTED_KEY = "_value_accepted"
INVALID_FEE_KEY = "_unpacking_fee_invalid"
ASKED_KEY = "_asked_field"

CONTAINER_QUESTION = "柜号是多少？（没有柜号请回复「无」）"
ASK_CONTAINER_AGAIN = "请直接发送柜号（如 MSCU1234567），或回复「无」。"
ASK_FEE_AGAIN = "请确认拆柜费金额（如「拆柜费 450」）。"

_NONE_WORDS = ("无", "没有", "沒有", "none", "n/a", "na", "无柜号", "没有柜号", "-")
_STANDARD = re.compile(r"^[A-Z]{4}\d{7}$")
_CONTAINER_SHAPED = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{4}[\s-]?\d[\d\s-]*\d(?![A-Za-z0-9])")
_CONTAINER_ANCHOR = re.compile(r"(?:柜号|箱号|ctn\s*#?|container)\s*[:：#]?\s*([A-Za-z0-9][A-Za-z0-9\s-]*[A-Za-z0-9]|[A-Za-z0-9])", re.I)
_REQUEST_ID = re.compile(r"REQ-\d{8}-\d+", re.I)
# "No container" said inside a longer message.
_NONE_PHRASE = re.compile(r"(?:无|没有|没)\s*柜号|柜号\s*[:：]?\s*(?:无|没有)|no\s+container", re.I)
_NUMBER = re.compile(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)?(?:\.\d*)?")
_QUANTITY_UNITS = ("箱", "托", "板", "件", "pcs", "ctn", "plt", "pallet", "box")
_CURRENCY_AFTER = ("usd", "美元", "美金", "元", "块")
_FEE_WORDS = ("拆柜费", "拆箱费", "拆柜", "费用", "收费", "收")
# Fee-specific only: a bare 不收 also appears in unrelated text (不收货了),
# and code acts on these without the AI (implementation audit round 2).
_NO_FEE_PHRASES = ("不收拆柜费", "不收费", "免拆柜费", "拆柜费免", "免收拆柜费", "无拆柜费", "没有拆柜费", "不需要拆柜费")
# A message that is nothing but one amount -- the usual reply to the fee
# question -- states the fee explicitly.
_AMOUNT_ONLY = re.compile(r"^\s*(?:\$|usd)?\s*-?[\d,]*\.?\d*\s*(?:usd|美元|美金|元|块)?\s*$", re.I)


# ── 柜号 ─────────────────────────────────────────────────────────────────────

def normalize_container(raw) -> str | None:
    """The canonical 柜号: 无 for every way of saying none, else uppercase
    with spaces and dashes removed. None when there's nothing usable."""
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value:
        return None
    if value.lower() in _NONE_WORDS:
        return NONE_VALUE
    value = re.sub(r"[\s\-]+", "", value).upper()
    return value or None


def is_standard(value: str | None) -> bool:
    return bool(value) and bool(_STANDARD.match(value))


def has_container(value) -> bool:
    return isinstance(value, str) and value not in ("", NONE_VALUE)


def _strip_request_ids(message: str) -> str:
    return _REQUEST_ID.sub(" ", message or "")


def container_candidates(message: str, *, answering: bool) -> list[str]:
    """
    柜号 values the user actually typed (R8/R9), normalized:
    - answering the bot's 柜号 question with a single token: that token, any
      shape (a non-standard value must reach the format check);
    - the token after a 柜号 word (柜号/箱号/ctn/container): any shape;
    - otherwise only container-shaped tokens (4 letters + digits).
    """
    text = _strip_request_ids(message).strip()
    if not text:
        return []
    if text.lower() in _NONE_WORDS:
        return [NONE_VALUE]
    found: list[str] = []
    if _NONE_PHRASE.search(text):
        found.append(NONE_VALUE)
    if answering and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\s-]*", text):
        found.append(normalize_container(text))
    for m in _CONTAINER_ANCHOR.finditer(text):
        token = m.group(1)
        # The anchored token ends where another field's words begin.
        token = re.split(r"\s+(?=[一-鿿])", token)[0]
        found.append(normalize_container(token))
    if not found:
        found += [normalize_container(m.group(0)) for m in _CONTAINER_SHAPED.finditer(text)]
    unique = []
    for value in found:
        if value and value not in unique:
            unique.append(value)
    return unique


# ── 拆柜费 ───────────────────────────────────────────────────────────────────

def _to_decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _anchored_and_bare(text: str):
    """Fee-eligible numbers in `text` (request IDs already stripped), split
    into anchored (a currency mark, or after a fee word) and bare. Numbers
    touching a letter or followed by a quantity unit are never fees (R6)."""
    anchored: list[tuple[str, Decimal | None]] = []
    bare: list[tuple[str, Decimal | None]] = []
    for m in _NUMBER.finditer(text):
        raw = m.group(0)
        if not raw or not re.search(r"\d", raw):
            continue
        start, end = m.start(), m.end()
        before, after = text[:start], text[end:]
        if (before[-1:].isalpha() and before[-1:].isascii()) or (after[:1].isalpha() and after[:1].isascii() and not after.lower().startswith("usd")):
            continue                                    # part of a 柜号 / SKU code
        tail = after.lstrip().lower()
        if any(tail.startswith(u) for u in _QUANTITY_UNITS):
            continue                                    # a quantity, not money
        value = _to_decimal(raw)
        head = before.rstrip()
        is_anchored = (
            head.endswith("$") or head.lower().endswith("usd")
            or any(tail.startswith(c) for c in _CURRENCY_AFTER)
            or any(re.sub(r"[\s:：]+$", "", head).endswith(w) for w in _FEE_WORDS)
        )
        (anchored if is_anchored else bare).append((raw, value))
    return anchored, bare


def _no_fee_stated(text: str) -> bool:
    return any(p in text for p in _NO_FEE_PHRASES) and not re.search(r"\d", text)


def amount_candidates(message: str) -> tuple[list[Decimal], list[str]]:
    """
    Field-aware scan for the fee (R4/R6). Returns (valid candidates,
    malformed texts). Anchored amounts are the only candidates when
    present; otherwise a single remaining bare number counts. A no-fee
    phrase (不收拆柜费) is 0.
    """
    text = _strip_request_ids(message or "")
    if _no_fee_stated(text):
        return [Decimal("0")], []
    anchored, bare = _anchored_and_bare(text)
    return _split_malformed(anchored or (bare if len(bare) == 1 else []))


def explicit_fee_candidates(message: str) -> tuple[list[Decimal], list[str]]:
    """
    Only fees the message states explicitly: anchored amounts and no-fee
    phrases (不收拆柜费 -> 0). Code takes one of these even when the AI
    extracted nothing (implementation audit: a typed "拆柜费 500" or
    "不收拆柜费" must never be ignored), and a message that is nothing but
    one amount ("500", "0", "$500"). A bare number inside other text is not
    explicit: it still needs the AI to have read it as the fee.
    """
    text = _strip_request_ids(message or "")
    if _no_fee_stated(text):
        return [Decimal("0")], []
    anchored, bare = _anchored_and_bare(text)
    if not anchored and len(bare) == 1 and _AMOUNT_ONLY.match(text):
        return _split_malformed(bare)
    return _split_malformed(anchored)


def _split_malformed(pool) -> tuple[list[Decimal], list[str]]:
    valid, malformed = [], []
    for raw, value in pool:
        if value is None or raw.startswith("-") or raw.endswith(".") or value.as_tuple().exponent < -2:
            malformed.append(raw)
        else:
            valid.append(value)
    return valid, malformed


def _ai_decimal(value) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("$", "").strip())
    except (InvalidOperation, ValueError):
        return None


def fee_number(value: Decimal):
    """JSON-friendly fee: an int when whole, else a 2-decimal float."""
    value = value.quantize(Decimal("0.01"))
    return int(value) if value == value.to_integral_value() else float(value)


def format_fee(value) -> str:
    amount = _ai_decimal(value) or Decimal("0")
    text = f"{amount.quantize(Decimal('0.01')):,.2f}"
    return "$" + (text[:-3] if text.endswith(".00") else text)


# ── Turn logic shared by both channels ───────────────────────────────────────

def _accepted(fields: dict, field: str, value) -> bool:
    return (fields.get(ACCEPTED_KEY) or {}).get(field) == value


def _set_pending(fields: dict, field: str, value) -> dict:
    return {**fields, PENDING_KEY: {"field": field, "value": value}}


def container_check_question(value: str) -> str:
    return (
        f"柜号「{value}」不是标准格式（4位字母+7位数字，如 MSCU1234567），请确认是否正确？"
        "回复「是」使用此柜号，或直接发送正确柜号。"
    )


def fee_check_question(value) -> str:
    return f"拆柜费 {format_fee(value)} 超出常见范围，请确认金额是否正确？回复「是」使用此金额，或直接发送正确金额。"


def fee_invalid_reply(raw: str) -> str:
    return f"拆柜费金额无效：{raw}（须为数字，最多两位小数），请重新输入。"


def apply_container(fields: dict, ai_value, message: str, *, answering: bool) -> tuple[dict, str | None]:
    """
    One turn's 柜号, from the typed message (R8): exactly one candidate is
    used whatever the AI returned; none or several -> ask again. A
    non-standard value opens a double-check unless already confirmed.
    Returns (fields, reply-or-None).
    """
    candidates = container_candidates(message, answering=answering)
    if ai_value in (None, "") and not candidates:
        return fields, None
    fields = {k: v for k, v in fields.items() if k != "container_number"}
    if len(candidates) != 1:
        return {**fields, ASKED_KEY: "container_number"}, ASK_CONTAINER_AGAIN
    value = candidates[0]
    if value == NONE_VALUE or is_standard(value) or _accepted(fields, "container_number", value):
        return {**fields, "container_number": value}, None
    return _set_pending(fields, "container_number", value), container_check_question(value)


def apply_fee(fields: dict, ai_value, message: str) -> tuple[dict, str | None]:
    """
    One turn's 拆柜费, validated against the typed message (R4/R6). Returns
    (fields, reply-or-None). An invalid amount is rejected out loud and the
    earlier amount removed (R3); over the cap opens a double-check (Q1).
    """
    candidates, malformed = amount_candidates(message)
    if malformed:
        cleared = {k: v for k, v in fields.items() if k != "unpacking_fee"}
        return {**cleared, INVALID_FEE_KEY: True}, fee_invalid_reply(malformed[0])
    ai_amount = _ai_decimal(ai_value)
    if ai_amount is None:
        # The AI missed it, but the message states a fee explicitly: use
        # that (exactly one), so a typed correction is never ignored.
        explicit, _ = explicit_fee_candidates(message)
        if not explicit:
            return fields, None
        if len(explicit) > 1:
            return {k: v for k, v in fields.items() if k != "unpacking_fee"}, ASK_FEE_AGAIN
        ai_amount = explicit[0]
    if ai_amount not in candidates:
        return {k: v for k, v in fields.items() if k != "unpacking_fee"}, ASK_FEE_AGAIN
    number = fee_number(ai_amount)
    cleared = {k: v for k, v in fields.items() if k not in ("unpacking_fee", INVALID_FEE_KEY)}
    if ai_amount > FEE_CAP and not _accepted(fields, "unpacking_fee", number):
        return _set_pending(cleared, "unpacking_fee", number), fee_check_question(number)
    return {**cleared, "unpacking_fee": number}, None


def resolve_pending(fields: dict, accept: bool) -> tuple[dict, str | None]:
    """
    The answer to an open double-check (R1): accept stores the value and
    records it as confirmed; reject clears it and asks for it again. Never
    touches anything else, so the caller's next step is the normal
    readiness step (a question or the confirmation), never execution.
    """
    pending = fields.get(PENDING_KEY) or {}
    field, value = pending.get("field"), pending.get("value")
    fields = {k: v for k, v in fields.items() if k != PENDING_KEY}
    if not field:
        return fields, None
    if accept:
        accepted = {**(fields.get(ACCEPTED_KEY) or {}), field: value}
        out = {**fields, field: value, ACCEPTED_KEY: accepted}
        if field == "unpacking_fee":
            out.pop(INVALID_FEE_KEY, None)
        return out, None
    fields.pop(field, None)
    if field == "container_number":
        return {**fields, ASKED_KEY: "container_number"}, CONTAINER_QUESTION
    return fields, "请重新输入拆柜费金额（美元）。"


def pending_question(fields: dict) -> str | None:
    """The open double-check's question, to ask again."""
    pending = fields.get(PENDING_KEY) or {}
    if pending.get("field") == "container_number":
        return container_check_question(pending.get("value"))
    if pending.get("field") == "unpacking_fee":
        return fee_check_question(pending.get("value"))
    return None


NEGATIVE_REPLIES = frozenset({"否", "不", "不对", "不是", "错了", "不正确", "no", "n"})


def is_negative(text: str) -> bool:
    return (text or "").strip().rstrip(" 。！!.~～").strip().lower() in NEGATIVE_REPLIES


def effective_container(completion_fields: dict, original_fields: dict):
    """The receipt's 柜号: the warehouse's (D6) or the original request's."""
    given = completion_fields.get("container_number")
    return given if given not in (None, "") else original_fields.get("container_number")


INBOUND_CONTAINER_SERVICES = frozenset({"uchoice_inbound_request", "confirm_inbound_completion"})


def apply_turn(context: dict, service_name: str, session, previous_fields: dict, ai_response) -> str | None:
    """
    The 柜号 / 拆柜费 rules for one inbound turn (request or receipt), after
    the AI's fields were merged. The AI's own container_number/unpacking_fee
    never persist directly: core.inbound_container reads them from the typed
    message (R4/R6/R8/R9). Returns a reply that ends the turn (a question, a
    double-check, a rejection), or None to continue with readiness.
    Shared by Kefu (core/kefu_turn_apply.py) and Smart Robot
    (core/workflow_engine.py).
    """
    import core.inbound_container as ic

    raw = dict(getattr(ai_response, "extracted_fields", None) or {})
    content = context.get("content") or ""
    is_receipt = service_name == "confirm_inbound_completion"
    fields = dict(session.collected_fields or {})
    for key in ("container_number", "unpacking_fee", "needs_unpacking"):
        if key in previous_fields and key != "needs_unpacking":
            fields[key] = previous_fields[key]
        else:
            fields.pop(key, None)
    answering = previous_fields.get(ic.ASKED_KEY) == "container_number"
    fields.pop(ic.ASKED_KEY, None)

    def done(new_fields: dict, reply: str | None) -> str | None:
        if not is_receipt and not ic.has_container(new_fields.get("container_number")) \
                and new_fields.get("container_number") != ic.NONE_VALUE and not new_fields.get(ic.PENDING_KEY):
            new_fields[ic.ASKED_KEY] = "container_number"     # the missing-field prompt asks it
        session.collected_fields = new_fields
        context["collected_fields"] = new_fields
        if reply is not None and session.status == "pending_confirmation":
            session.status = "active"
        return reply

    resolution = context.pop("_pending_resolution", None)
    if resolution is not None:
        return done(*ic.resolve_pending(fields, accept=resolution == "accept"))

    pending = fields.get(ic.PENDING_KEY)
    if pending:
        # A new value for the questioned field replaces the check; anything
        # else (including an AI "confirm") asks the same question again.
        field = pending.get("field")
        new_value = (
            ic.container_candidates(content, answering=True) if field == "container_number"
            else ic.amount_candidates(content)[0] or ic.amount_candidates(content)[1]
        )
        if not new_value:
            return done(fields, ic.pending_question(fields))
        fields.pop(ic.PENDING_KEY, None)
        answering = True

    fields, reply = ic.apply_container(fields, raw.get("container_number"), content, answering=answering)
    if is_receipt and not fields.get(ic.PENDING_KEY):
        fields, fee_reply = ic.apply_fee(fields, raw.get("unpacking_fee"), content)
        reply = reply or fee_reply
    return done(fields, reply)
