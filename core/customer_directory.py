"""
Customer master-data module boundary. Callers (handlers, admin routes) go
through these functions only -- never a raw query against customer/
customer_credential from elsewhere. This is the seam that makes a future
extraction to a real standalone service (once a second real consumer like
TWF_Invoice_Parser needs one) a matter of swapping these implementations
for an HTTP client behind the same signatures, not a redesign.

See docs/reviews/active/2026-09-customer-service-and-label-pipeline/plan.md
(gitignored, not tracked) for the full design rationale.
"""
import base64
import os
from dataclasses import dataclass
from decimal import Decimal

from Crypto.Cipher import AES
from sqlalchemy.orm import Session as DBSession

import config
from models.customer import Customer, CustomerCredential

_VALID_CREDENTIAL_TYPES = {"oms_app_key", "oms_app_secret", "ydd_username", "ydd_password"}


@dataclass
class CustomerRecord:
    customer_id: str
    display_name: str
    display_name_cn: str | None
    contact_name: str | None
    email: str | None
    phone: str | None
    addr_line1: str | None
    city: str | None
    state: str | None
    zip: str | None
    country: str | None
    bank_account: str | None
    status: str
    rate_multiplier: dict
    ydd_channel_id: dict
    oms_wh_code: str | None
    toggles: dict
    notes: str | None


def _to_record(row: Customer) -> CustomerRecord:
    return CustomerRecord(
        customer_id=row.customer_id,
        display_name=row.display_name,
        display_name_cn=row.display_name_cn,
        contact_name=row.contact_name,
        email=row.email,
        phone=row.phone,
        addr_line1=row.addr_line1,
        city=row.city,
        state=row.state,
        zip=row.zip,
        country=row.country,
        bank_account=row.bank_account,
        status=row.status,
        rate_multiplier=row.rate_multiplier or {},
        ydd_channel_id=row.ydd_channel_id or {},
        oms_wh_code=row.oms_wh_code,
        toggles=row.toggles or {},
        notes=row.notes,
    )


def get_customer(db: DBSession, customer_id: str) -> CustomerRecord | None:
    row = db.get(Customer, customer_id)
    return _to_record(row) if row else None


def list_customers(db: DBSession, status: str | None = None) -> list[CustomerRecord]:
    query = db.query(Customer)
    if status is not None:
        query = query.filter_by(status=status)
    return [_to_record(row) for row in query.order_by(Customer.customer_id).all()]


def upsert_customer(db: DBSession, customer_id: str, actor: str, **fields) -> CustomerRecord:
    """
    actor identifies who made this change (admin key holder, e.g. a display
    name/identifier) for created_by/updated_by -- required, matching the
    request_logger.py pattern of recording provenance at write time rather
    than defaulting it later.
    """
    row = db.get(Customer, customer_id)
    if row is None:
        row = Customer(customer_id=customer_id, created_by=actor, **fields)
        db.add(row)
    else:
        for key, value in fields.items():
            setattr(row, key, value)
        row.updated_by = actor
    db.commit()
    db.refresh(row)
    return _to_record(row)


def get_margin(db: DBSession, customer_id: str, carrier: str) -> Decimal | None:
    """
    Reserved for the future direct-carrier-API pickup pipeline -- NOT read
    by label creation today (YiDiDa already returns its own sales amount,
    used directly; see label_shipment.sales_amount).
    """
    row = db.get(Customer, customer_id)
    if row is None:
        return None
    value = (row.rate_multiplier or {}).get(carrier)
    return Decimal(str(value)) if value is not None else None


def get_toggle(db: DBSession, customer_id: str, toggle_name: str, default: bool = False) -> bool:
    row = db.get(Customer, customer_id)
    if row is None:
        return default
    return (row.toggles or {}).get(toggle_name, default)


# ── Credentials ──────────────────────────────────────────────────────────
# Application-level AES-256-GCM. The master key lives only in
# config.CUSTOMER_CREDENTIAL_KEY (an env var) -- never in the database
# alongside the ciphertext it protects. key_version exists so a future
# master-key rotation can be applied incrementally rather than breaking
# every stored credential at once; only version 1 is implemented today.

_NONCE_LEN = 12


def _get_key(key_version: int) -> bytes:
    if key_version != 1:
        raise RuntimeError(f"Unknown CUSTOMER_CREDENTIAL_KEY version: {key_version}")
    key = base64.b64decode(config.CUSTOMER_CREDENTIAL_KEY)
    if len(key) != 32:
        raise RuntimeError("CUSTOMER_CREDENTIAL_KEY must decode to exactly 32 bytes (AES-256)")
    return key


def _encrypt(plaintext: str, key_version: int = 1) -> bytes:
    key = _get_key(key_version)
    nonce = os.urandom(_NONCE_LEN)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext.encode("utf-8"))
    return nonce + tag + ciphertext


def _decrypt(blob: bytes, key_version: int) -> str:
    key = _get_key(key_version)
    nonce, tag, ciphertext = blob[:_NONCE_LEN], blob[_NONCE_LEN:_NONCE_LEN + 16], blob[_NONCE_LEN + 16:]
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")


def set_credential(db: DBSession, customer_id: str, credential_type: str, plaintext_value: str, actor: str) -> None:
    """
    Write-only from the caller's perspective -- this function never returns
    the value back. The plaintext only exists in memory for the duration of
    this call.
    """
    if credential_type not in _VALID_CREDENTIAL_TYPES:
        raise ValueError(f"Unknown credential_type: {credential_type!r}")
    encrypted = _encrypt(plaintext_value)
    row = db.get(CustomerCredential, (customer_id, credential_type))
    if row is None:
        row = CustomerCredential(
            customer_id=customer_id, credential_type=credential_type,
            encrypted_value=encrypted, key_version=1, created_by=actor,
        )
        db.add(row)
    else:
        row.encrypted_value = encrypted
        row.key_version = 1
        row.updated_by = actor
    db.commit()


def get_credentials(db: DBSession, customer_id: str) -> dict:
    """
    Decrypted, in-memory only -- internal use (label/OMS handlers). Never
    call this to serve a value back through an API response.
    """
    rows = db.query(CustomerCredential).filter_by(customer_id=customer_id).all()
    return {row.credential_type: _decrypt(row.encrypted_value, row.key_version) for row in rows}


def resolve_billing_customer_id(
    db: DBSession, is_customer_role: bool, requester_billing_customer_id: str | None, extracted_value: str | None,
) -> tuple[str | None, str | None]:
    """
    Shared by both channels' turn-processing pipelines (core/kefu_turn_apply.py
    for Kefu, core/workflow_engine.py for Smart Bot) for resolving
    fedex_label/ups_label's billing_customer_id field, AND by the label
    handlers themselves (handlers/label/base.py) to revalidate at
    confirmation time before calling YDD -- never trust a value collected
    on an earlier turn without rechecking it here.

    is_customer_role must be the CALLER'S OWN role, independent of whether
    requester_billing_customer_id happens to be set -- this is the fix for
    a real gap: previously, an unbound customer-role member (no admin-side
    binding support existed yet) fell through to the "validate whatever
    was extracted" branch below, silently letting them select ANY active
    customer's billing account just by typing its F###### code. A
    customer-role caller is now REJECTED outright if their own binding
    isn't set -- never treated as staff, regardless of what they typed.

    If is_customer_role and requester_billing_customer_id IS set, that
    value is AUTHORITATIVE -- always used, on every turn, regardless of
    what the AI extracted from the conversation. A customer cannot state a
    different billing_customer_id and have it accepted; this is who they
    are, not something they report about themselves.

    Otherwise (staff/admin/Kefu creating a label on behalf of someone
    else), the extracted value is validated against the real customer
    directory (must exist, must be status='active') before being accepted.

    Returns (resolved_id, error_message). resolved_id is None with no error
    only in the genuine "nothing provided yet" wait case for a non-customer
    caller -- every customer-role path either resolves or errors, never
    silently waits, since their own binding either already exists or is an
    admin-fixable account problem, not something the conversation will
    ever supply.
    """
    if is_customer_role:
        if not requester_billing_customer_id:
            return None, "您的账户尚未绑定客户身份，请联系管理员完成绑定后再试。"
        record = get_customer(db, requester_billing_customer_id)
        if record is None or record.status != "active":
            return None, "您的账户尚未正确关联有效客户身份，请联系管理员处理后再试。"
        return requester_billing_customer_id, None

    if not extracted_value:
        return None, None

    record = get_customer(db, extracted_value)
    if record is None:
        return None, f"未找到客户编号 {extracted_value}，请确认后重新提供。"
    if record.status != "active":
        return None, f"客户 {extracted_value} 当前状态为「{record.status}」，无法为其创建标签，请联系管理员。"
    return extracted_value, None


def has_credentials(db: DBSession, customer_id: str, *credential_types: str) -> bool:
    """Presence check without decrypting -- used for the OMS skip-if-absent gate (no need to touch plaintext just to know whether it exists)."""
    count = (
        db.query(CustomerCredential)
        .filter(CustomerCredential.customer_id == customer_id, CustomerCredential.credential_type.in_(credential_types))
        .count()
    )
    return count == len(credential_types)
