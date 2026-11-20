"""
Customer pickup (core/pickup.py) -- docs/ai-collaboration/2026-11-customer-pickup/plan.md.

Offline: an in-memory SQLite database holding only the address table, seeded
with the four known self-pickup rows plus one ordinary address.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core import pickup
from core.uchoice_context import address_candidates
from models.uchoice import UchoiceAddress

JFK_RET = uuid.UUID("bb8c35cb-dda1-404d-9294-682ed3df5659")
DE_RET = uuid.UUID("a25bb922-2340-41ac-8e2d-f0a5192d85d0")
GENERAL = uuid.UUID("a1000000-0042-0000-0000-000000000001")
LEGACY = uuid.UUID("5a1cd32c-f2a3-492d-b2c3-360acfff7514")
# Not all digits: SQLite's affinity rules would turn an all-digit hex string into a float.
PLAIN = uuid.UUID("c0ffee00-1111-4111-8111-111111111111")


def _row(address_id, company, charge, addr, wh, kind):
    return UchoiceAddress(
        address_id=address_id, company_name=company, charge_type=charge, addr=addr,
        warehouse_code=wh, created_by="test", pickup_kind=kind,
        created_at=datetime.now(timezone.utc),   # SQLite has no now() server default
    )


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    UchoiceAddress.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        _row(JFK_RET, "JFK仓库自提留存", "self_pickup", "14502 156th St", "JFK", pickup.KIND_RETENTION),
        _row(DE_RET, "DE仓库自提留存", "self_pickup", "201 Gabor DR", "DE", pickup.KIND_RETENTION),
        _row(GENERAL, "客户自提", "self_pickup", "", None, pickup.KIND_CUSTOMER),
        _row(LEGACY, "散客", "self_pickup", "", None, pickup.KIND_LEGACY),
        _row(PLAIN, "ABC Trading", "delivery", "123 Main St, Queens, NY 11101", "JFK", None),
    ])
    session.commit()
    yield session
    session.close()


CTX = {"content": "", "warehouse_codes": None}


def _ctx(content, **extra):
    return {**CTX, "content": content, **extra}


# ── vocabulary ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["张三来仓库自提", "客户自取", "他自己来拿", "客户来取货", "到仓提货", "will call", "customer pick-up"])
def test_pickup_wording_is_positive(text):
    assert pickup.vocabulary_flags(text) == (True, False)


@pytest.mark.parametrize("text", ["不是自提，送到 X", "不用自提了", "非自提", "不自取"])
def test_negated_pickup_wording_is_negated_not_positive(text):
    assert pickup.vocabulary_flags(text) == (False, True)


def test_plain_delivery_has_no_pickup_wording():
    assert pickup.vocabulary_flags("发货到 ABC Trading，S2 3箱") == (False, False)


def test_wording_inside_a_known_address_name_is_ignored():
    assert pickup.vocabulary_flags("发到自提之家", address_texts=["自提之家"]) == (False, False)


# ── intent_this_turn (effective mode + backstop) ──────────────────────────────

def test_ai_says_pickup_with_pickup_words_is_accepted():
    assert pickup.intent_this_turn("customer", "none", "张三来自提") == "customer"


def test_ai_says_pickup_without_any_pickup_words_asks():
    assert pickup.intent_this_turn("customer", "none", "发给张三 S2 3箱") == "ask"


def test_name_only_followup_keeps_the_persisted_pickup_mode():
    assert pickup.intent_this_turn(None, "customer", "张三") == "customer"
    assert pickup.intent_this_turn("customer", "customer", "张三") == "customer"


def test_ai_silent_but_message_says_pickup_asks():
    assert pickup.intent_this_turn(None, "none", "客户自提") == "ask"


def test_ai_says_delivery_but_message_says_pickup_asks():
    assert pickup.intent_this_turn("none", "none", "客户自提") == "ask"


def test_negated_pickup_with_delivery_intent_is_a_valid_revision():
    assert pickup.intent_this_turn("none", "customer", "不是自提，送到 X 公司") == "none"


def test_negated_pickup_but_ai_kept_pickup_asks():
    assert pickup.intent_this_turn(None, "customer", "不是自提，送到 X 公司") == "ask"


def test_plain_delivery_stays_delivery():
    assert pickup.intent_this_turn(None, "none", "发货到 ABC Trading") == "none"


# ── sanitizer ─────────────────────────────────────────────────────────────────

def test_sanitizer_drops_invalid_type_and_cleans_name(db):
    out = pickup.sanitize_extracted({"pickup_type": "BOGUS", "pickup_customer_name": "  王  小明\n"}, db)
    assert "pickup_type" not in out
    assert out["pickup_customer_name"] == "王 小明"


def test_sanitizer_normalizes_type_case_and_drops_blank_name(db):
    out = pickup.sanitize_extracted({"pickup_type": " Customer ", "pickup_customer_name": "   "}, db)
    assert out == {"pickup_type": "customer"}


def test_sanitizer_bounds_the_name(db):
    out = pickup.sanitize_extracted({"pickup_customer_name": "长" * 200}, db)
    assert len(out["pickup_customer_name"]) == pickup.NAME_MAX_LEN


@pytest.mark.parametrize("bad_id", [GENERAL, JFK_RET, LEGACY])
def test_sanitizer_strips_an_ai_written_pickup_row_id(db, bad_id):
    out = pickup.sanitize_extracted({"destination_address_id": str(bad_id), "sku_lines": []}, db)
    assert "destination_address_id" not in out and "sku_lines" in out


def test_sanitizer_keeps_an_ordinary_destination_id(db):
    out = pickup.sanitize_extracted({"destination_address_id": str(PLAIN)}, db)
    assert out["destination_address_id"] == str(PLAIN)


def test_sanitizer_is_applied_through_the_shared_entry_point(db):
    from core.uchoice_field_sanitization import sanitize_extracted_fields_before_persistence
    out = sanitize_extracted_fields_before_persistence(
        "uchoice_outbound_request", {"destination_address_id": str(GENERAL), "pickup_type": "customer"}, db)
    assert out == {"pickup_type": "customer"}
    # other services are untouched
    other = sanitize_extracted_fields_before_persistence("upsert_address", {"pickup_type": "customer"}, db)
    assert other == {"pickup_type": "customer"}


# ── resolver ──────────────────────────────────────────────────────────────────

def test_customer_resolves_to_the_one_general_row_for_any_warehouse(db):
    assert pickup.resolve_destination(db, "customer", "JFK") == str(GENERAL)
    assert pickup.resolve_destination(db, "customer", None) == str(GENERAL)


def test_retention_resolves_only_to_the_source_warehouses_own_row(db):
    assert pickup.resolve_destination(db, "retention", "JFK") == str(JFK_RET)
    assert pickup.resolve_destination(db, "retention", "DE") == str(DE_RET)
    assert pickup.resolve_destination(db, "retention", "NJ") is None      # no fallback to another warehouse
    assert pickup.resolve_destination(db, "retention", None) is None


def test_legacy_row_is_never_returned(db):
    db.query(UchoiceAddress).filter(UchoiceAddress.address_id == GENERAL).delete()
    db.commit()
    assert pickup.resolve_destination(db, "customer", "JFK") is None


def test_resolver_fails_closed_when_two_rows_match(db):
    db.add(_row(uuid.uuid4(), "客户自提2", "self_pickup", "", None, pickup.KIND_CUSTOMER))
    db.commit()
    assert pickup.resolve_destination(db, "customer", "JFK") is None


# ── apply_turn (transition step) ──────────────────────────────────────────────

def test_initial_customer_pickup_resolves_the_row_and_stamps_the_marker(db):
    previous = {"warehouse_code": "JFK"}
    extracted = {"pickup_type": "customer", "pickup_customer_name": "王小明"}
    merged = {**previous, **extracted}
    fields, ask = pickup.apply_turn(db, _ctx("王小明来自提"), previous, merged, extracted)
    assert ask is None
    assert fields["destination_address_id"] == str(GENERAL)
    assert fields["pickup_customer_name"] == "王小明"
    assert fields[pickup.MARKER_KEY] == pickup.MARKER_VALUE


def test_pickup_wins_over_an_ordinary_destination_the_ai_also_gave(db):
    previous = {"warehouse_code": "JFK"}
    extracted = {"pickup_type": "customer", "pickup_customer_name": "王小明", "destination_address_id": str(PLAIN)}
    fields, ask = pickup.apply_turn(db, _ctx("王小明来自提，他公司在 123 Main St"), previous, {**previous, **extracted}, extracted)
    assert ask is None and fields["destination_address_id"] == str(GENERAL)


def test_retention_resolves_by_warehouse_and_drops_any_name(db):
    previous = {"warehouse_code": "DE"}
    extracted = {"pickup_type": "retention", "pickup_customer_name": "张三"}
    fields, ask = pickup.apply_turn(db, _ctx("DE仓库自提留存"), previous, {**previous, **extracted}, extracted)
    assert ask is None
    assert fields["destination_address_id"] == str(DE_RET)
    assert "pickup_customer_name" not in fields


def test_retention_uses_the_callers_default_warehouse_when_none_stated(db):
    extracted = {"pickup_type": "retention"}
    fields, _ = pickup.apply_turn(db, _ctx("自提留存", warehouse_codes=["DE"]), {}, dict(extracted), extracted)
    assert fields["destination_address_id"] == str(DE_RET)


def test_retention_waits_when_the_warehouse_cannot_be_decided(db):
    extracted = {"pickup_type": "retention"}
    fields, ask = pickup.apply_turn(db, _ctx("自提留存", warehouse_codes=["DE", "JFK"]), {}, dict(extracted), extracted)
    assert ask is None and "destination_address_id" not in fields
    # next turn: the warehouse is now stated, the persisted mode resolves
    later_prev = dict(fields)
    later_merged = {**later_prev, "warehouse_code": "JFK"}
    fields2, _ = pickup.apply_turn(db, _ctx("JFK", warehouse_codes=["DE", "JFK"]), later_prev, later_merged, {"warehouse_code": "JFK"})
    assert fields2["destination_address_id"] == str(JFK_RET)


def test_name_only_followup_keeps_the_pickup_and_the_destination(db):
    previous = {"warehouse_code": "JFK", "pickup_type": "customer", "destination_address_id": str(GENERAL),
                pickup.MARKER_KEY: 1}
    extracted = {"pickup_customer_name": "王小明"}
    fields, ask = pickup.apply_turn(db, _ctx("王小明"), previous, {**previous, **extracted}, extracted)
    assert ask is None
    assert fields["pickup_type"] == "customer" and fields["destination_address_id"] == str(GENERAL)
    assert fields["pickup_customer_name"] == "王小明"


def test_revision_to_delivery_clears_pickup_fields_and_the_pickup_row(db):
    previous = {"warehouse_code": "JFK", "pickup_type": "customer", "pickup_customer_name": "王小明",
                "destination_address_id": str(GENERAL), pickup.MARKER_KEY: 1}
    extracted = {"pickup_type": "none"}
    fields, ask = pickup.apply_turn(db, _ctx("不是自提，送到 ABC Trading"), previous, {**previous, **extracted}, extracted)
    assert ask is None
    for gone in ("pickup_type", "pickup_customer_name", "destination_address_id"):
        assert gone not in fields


def test_revision_to_delivery_keeps_a_new_ordinary_destination(db):
    previous = {"warehouse_code": "JFK", "pickup_type": "customer", "pickup_customer_name": "王小明",
                "destination_address_id": str(GENERAL), pickup.MARKER_KEY: 1}
    extracted = {"pickup_type": "none", "destination_address_id": str(PLAIN)}
    merged = {**previous, **extracted}
    fields, _ = pickup.apply_turn(db, _ctx("不是自提，送到 ABC Trading"), previous, merged, extracted)
    assert fields["destination_address_id"] == str(PLAIN) and "pickup_type" not in fields


def test_revision_to_pickup_clears_a_delivery_destination(db):
    previous = {"warehouse_code": "JFK", "destination_address_id": str(PLAIN)}
    extracted = {"pickup_type": "customer", "pickup_customer_name": "王小明"}
    fields, _ = pickup.apply_turn(db, _ctx("这不是送货，是客户自己来提货，王小明"), previous, {**previous, **extracted}, extracted)
    assert fields["destination_address_id"] == str(GENERAL)


def test_disagreement_returns_the_question_and_leaves_pickup_fields_as_they_were(db):
    previous = {"warehouse_code": "JFK"}
    extracted = {"pickup_type": "customer", "pickup_customer_name": "王小明"}
    fields, ask = pickup.apply_turn(db, _ctx("发给王小明 S2 3箱"), previous, {**previous, **extracted}, extracted)
    assert ask == pickup.ASK_WHICH
    assert "pickup_type" not in fields and "pickup_customer_name" not in fields
    assert "destination_address_id" not in fields


def test_bare_ziti_with_no_ai_classification_asks_which_type(db):
    fields, ask = pickup.apply_turn(db, _ctx("S2 3箱 自提"), {"warehouse_code": "JFK"}, {"warehouse_code": "JFK"}, {})
    assert ask == pickup.ASK_WHICH


def test_ordinary_delivery_turn_is_untouched(db):
    previous = {"warehouse_code": "JFK"}
    extracted = {"destination_address_id": str(PLAIN)}
    merged = {**previous, **extracted}
    fields, ask = pickup.apply_turn(db, _ctx("发到 ABC Trading"), previous, merged, extracted)
    assert ask is None and fields == merged


# ── final-combination validator ───────────────────────────────────────────────

def _strict(**kw):
    return {pickup.MARKER_KEY: 1, "warehouse_code": "JFK", **kw}


def test_request_without_the_marker_is_grandfathered_for_any_destination(db):
    for dest in (JFK_RET, LEGACY, GENERAL, PLAIN):
        assert pickup.validate_combination({"warehouse_code": "JFK", "destination_address_id": str(dest)}, db) is None


def test_consistent_customer_pickup_passes(db):
    fields = _strict(pickup_type="customer", pickup_customer_name="王小明", destination_address_id=str(GENERAL))
    assert pickup.validate_combination(fields, db) is None


def test_customer_pickup_without_a_name_is_rejected_with_the_walk_in_hint(db):
    fields = _strict(pickup_type="customer", destination_address_id=str(GENERAL))
    assert "散客" in pickup.validate_combination(fields, db)


def test_customer_pickup_on_the_wrong_row_is_rejected(db):
    fields = _strict(pickup_type="customer", pickup_customer_name="王小明", destination_address_id=str(PLAIN))
    assert pickup.validate_combination(fields, db) is not None


def test_consistent_retention_passes_and_wrong_warehouse_row_is_rejected(db):
    ok = _strict(pickup_type="retention", destination_address_id=str(JFK_RET))
    assert pickup.validate_combination(ok, db) is None
    wrong = _strict(pickup_type="retention", destination_address_id=str(DE_RET))
    assert pickup.validate_combination(wrong, db) is not None


def test_retention_with_a_name_asks_which_was_meant(db):
    fields = _strict(pickup_type="retention", pickup_customer_name="张三", destination_address_id=str(JFK_RET))
    assert "客户自提" in pickup.validate_combination(fields, db)


@pytest.mark.parametrize("pickup_row", [GENERAL, JFK_RET, LEGACY])
def test_new_request_with_no_type_and_a_pickup_destination_is_rejected(db, pickup_row):
    assert pickup.validate_combination(_strict(destination_address_id=str(pickup_row)), db) is not None


def test_new_ordinary_delivery_passes_and_a_stray_name_is_questioned(db):
    assert pickup.validate_combination(_strict(destination_address_id=str(PLAIN)), db) is None
    assert pickup.validate_combination(_strict(destination_address_id=str(PLAIN), pickup_customer_name="王小明"), db) is not None


def test_the_pre_confirm_validator_wraps_the_combination_check(db):
    from core import pre_confirm_validators as pcv
    bad = _strict(pickup_type="customer", destination_address_id=str(GENERAL))   # no name
    assert pcv._valid_pickup_combination({}, bad, db) is not None
    good = _strict(pickup_type="customer", pickup_customer_name="王小明", destination_address_id=str(GENERAL))
    assert pcv._valid_pickup_combination({}, good, db) is None


def test_the_combination_check_is_part_of_the_outbound_validator_chain():
    import inspect
    from core import pre_confirm_validators as pcv
    source = inspect.getsource(pcv)
    chain = source.split('"uchoice_outbound_request": _compose(', 1)[1].split("),", 1)[0]
    assert "_valid_pickup_combination" in chain


# ── the pickup rows are system-managed (upsert_address) ───────────────────────

@pytest.mark.parametrize("row", [GENERAL, JFK_RET, LEGACY])
def test_upsert_address_cannot_update_a_pickup_row(db, row):
    from core import pre_confirm_validators as pcv
    error = pcv._valid_upsert_address_not_pickup_row({}, {"matched_address_id": str(row)}, db)
    assert error == pcv.PICKUP_ROW_NOT_EDITABLE_MESSAGE


def test_upsert_address_can_still_update_an_ordinary_address_and_create_new_ones(db):
    from core import pre_confirm_validators as pcv
    assert pcv._valid_upsert_address_not_pickup_row({}, {"matched_address_id": str(PLAIN)}, db) is None
    assert pcv._valid_upsert_address_not_pickup_row({}, {"company_name": "New Co"}, db) is None


def test_the_upsert_address_chain_includes_the_pickup_guard():
    import inspect
    from core import pre_confirm_validators as pcv
    chain = inspect.getsource(pcv).split('"upsert_address": _compose(', 1)[1].split("),", 1)[0]
    assert "_valid_upsert_address_not_pickup_row" in chain


def test_the_upsert_handler_refuses_a_pickup_row_at_execution_time(db):
    from handlers.uchoice.address import UpsertAddressHandler
    # (a UUID object, not a string: SQLite's UUID type needs one; PostgreSQL accepts either)
    context = {"collected_fields": {"matched_address_id": GENERAL, "company_name": "Hijacked"}, "role": "admin"}
    with pytest.raises(RuntimeError, match="系统自提地址"):
        UpsertAddressHandler().handle(context, {}, db)
    assert db.query(UchoiceAddress).filter(UchoiceAddress.address_id == GENERAL).one().company_name == "客户自提"


# ── candidate list ────────────────────────────────────────────────────────────

def test_outbound_candidates_exclude_every_pickup_row(db):
    ids = {c["address_id"] for c in address_candidates(db, source_warehouse_code="JFK", exclude_pickup=True)}
    assert ids == {str(PLAIN)}


def test_upsert_address_candidates_keep_the_full_book(db):
    ids = {c["address_id"] for c in address_candidates(db)}
    assert {str(GENERAL), str(JFK_RET), str(LEGACY), str(PLAIN)} <= ids


# ── marker + display ──────────────────────────────────────────────────────────

def test_new_outbound_requests_are_stamped_and_other_services_are_not():
    assert pickup.new_request_fields("uchoice_outbound_request") == {pickup.MARKER_KEY: pickup.MARKER_VALUE}
    assert pickup.new_request_fields("uchoice_inbound_request") == {}


def test_display_helpers():
    cust = {"pickup_type": "customer", "pickup_customer_name": "王小明"}
    assert pickup.list_label(cust) == "【自提】客户自提：王小明（无需送货）"
    assert pickup.confirmation_destination(cust) == "客户自提 — 取货人：王小明"
    assert pickup.invoice_destination(cust) == "客户自提（王小明）"
    assert pickup.list_label({"pickup_type": "retention"}) == "【自提】仓库自提留存（货不出仓）"
    assert pickup.list_label({}) is None and pickup.invoice_destination({"pickup_type": "retention"}) is None
    assert pickup.warning_line(cust).startswith("⚠️ 自提：无需送货")
    long_name = {"pickup_type": "customer", "pickup_customer_name": "长" * 40}
    assert pickup.list_label(long_name).count("长") <= pickup.LIST_NAME_MAX_LEN
