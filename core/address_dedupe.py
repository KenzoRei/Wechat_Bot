"""
Duplicate guard for add_address.

An address is a *business entry*, not a unique physical place: the same
street address legitimately exists more than once with a different
warehouse or charge type (e.g. a truck-transfer address and a self-pickup
one -- see core/uchoice_context.address_candidates). So the duplicate key is

    (customer_id, normalised addr, warehouse_code, charge_type)

compared NULL-safely (two NULL customers/warehouses are the same scope).
This is deliberately NOT semantic de-duplication: abbreviations
("Street" vs "St") and re-ordered parts are different keys. Normalisation is
conservative -- it never erases characters that distinguish units
(`-`, `#`, digits), so apartment "1-2" is not "12".

Two layers use it: core.pre_confirm_validators (a friendly message before
the confirmation card) and handlers.uchoice.address (the authoritative
execution-time recheck, serialised per key by an advisory lock so two
concurrent sessions cannot both insert).
"""
import re
import unicodedata

_SEPARATORS = re.compile(r"[,.;]+")
_WHITESPACE = re.compile(r"\s+")


def normalize_addr(addr: str | None) -> str:
    """Lowercase, NFKC, treat , . ; as separators, collapse whitespace."""
    text = unicodedata.normalize("NFKC", addr or "").lower()
    text = _SEPARATORS.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def duplicate_key(customer_id, addr, warehouse_code, charge_type) -> tuple:
    return (
        str(customer_id) if customer_id is not None else None,
        normalize_addr(addr),
        warehouse_code or None,
        charge_type or None,
    )


def lock_key_text(key: tuple) -> str:
    return "add_address|" + "|".join("" if part is None else str(part) for part in key)


def find_duplicate(db, customer_id, addr, warehouse_code, charge_type):
    """The existing UchoiceAddress with the same key, or None.

    customer_id / warehouse_code are matched NULL-safely (IS NULL when None).
    The normalised-addr comparison happens in Python: the address book is
    small and SQL cannot express NFKC + separator normalisation portably.
    """
    from models.uchoice import UchoiceAddress

    wanted = duplicate_key(customer_id, addr, warehouse_code, charge_type)
    query = db.query(UchoiceAddress)
    query = query.filter(UchoiceAddress.customer_id.is_(None) if customer_id is None
                         else UchoiceAddress.customer_id == customer_id)
    query = query.filter(UchoiceAddress.warehouse_code.is_(None) if not warehouse_code
                         else UchoiceAddress.warehouse_code == warehouse_code)
    query = query.filter(UchoiceAddress.charge_type == charge_type)
    for row in query.all():
        if duplicate_key(row.customer_id, row.addr, row.warehouse_code, row.charge_type) == wanted:
            return row
    return None


def lock_for_key(db, key: tuple) -> None:
    """Serialise concurrent adds of the same key (PostgreSQL only; no-op elsewhere)."""
    from sqlalchemy import text

    get_bind = getattr(db, "get_bind", None)
    bind = get_bind() if get_bind is not None else None
    if bind is not None and bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": lock_key_text(key)})


def duplicate_message(existing) -> str:
    label = f"{existing.company_name}（{existing.addr}）" if existing.company_name else existing.addr
    return (
        f"该地址已存在：{label}。无需重复新增；如需修改，请使用「更新地址」"
        "（需要相应权限，没有权限请联系管理员）。"
    )
