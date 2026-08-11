"""Envelope-neutral WeCom callback cryptography.

The Smart Robot JSON and Kefu XML adapters share these primitives.  This
module deliberately knows nothing about either envelope format.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
from dataclasses import dataclass

from Crypto.Cipher import AES


BLOCK_SIZE = 32


class WeComCryptoError(ValueError):
    """Base class for deterministic callback validation failures."""


class InvalidEncodingKey(WeComCryptoError):
    pass


class InvalidSignature(WeComCryptoError):
    pass


class InvalidCiphertext(WeComCryptoError):
    pass


class ReceiveIdMismatch(WeComCryptoError):
    pass


@dataclass(frozen=True)
class DecryptedPayload:
    content: str
    receive_id: str


def decode_encoding_aes_key(encoding_aes_key: str) -> bytes:
    """Decode the 43-character WeCom EncodingAESKey into 32 bytes."""
    try:
        key = base64.b64decode(encoding_aes_key + "=", validate=True)
    except Exception as exc:  # binascii.Error and malformed input
        raise InvalidEncodingKey("invalid EncodingAESKey") from exc
    if len(key) != 32:
        raise InvalidEncodingKey("EncodingAESKey must decode to 32 bytes")
    return key


def compute_signature(token: str, timestamp: str, nonce: str, encrypted: str) -> str:
    parts = sorted((str(token), str(timestamp), str(nonce), str(encrypted)))
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


def verify_signature(
    token: str,
    timestamp: str,
    nonce: str,
    encrypted: str,
    supplied_signature: str,
) -> None:
    expected = compute_signature(token, timestamp, nonce, encrypted)
    if not hmac.compare_digest(expected, str(supplied_signature)):
        raise InvalidSignature("callback signature mismatch")


def _pad(data: bytes) -> bytes:
    amount = BLOCK_SIZE - (len(data) % BLOCK_SIZE)
    return data + bytes((amount,)) * amount


def _unpad(data: bytes) -> bytes:
    if not data:
        raise InvalidCiphertext("empty decrypted payload")
    amount = data[-1]
    if amount < 1 or amount > BLOCK_SIZE or len(data) < amount:
        raise InvalidCiphertext("invalid PKCS#7 padding")
    if data[-amount:] != bytes((amount,)) * amount:
        raise InvalidCiphertext("invalid PKCS#7 padding")
    return data[:-amount]


def encrypt_payload(content: str, receive_id: str, aes_key: bytes) -> str:
    content_bytes = content.encode("utf-8")
    framed = (
        os.urandom(16)
        + struct.pack("!I", len(content_bytes))
        + content_bytes
        + receive_id.encode("utf-8")
    )
    cipher = AES.new(aes_key, AES.MODE_CBC, aes_key[:16])
    return base64.b64encode(cipher.encrypt(_pad(framed))).decode("ascii")


def decrypt_payload(ciphertext: str, expected_receive_id: str, aes_key: bytes) -> DecryptedPayload:
    try:
        encrypted = base64.b64decode(ciphertext, validate=True)
        if not encrypted or len(encrypted) % AES.block_size:
            raise InvalidCiphertext("ciphertext length is invalid")
        cipher = AES.new(aes_key, AES.MODE_CBC, aes_key[:16])
        plain = _unpad(cipher.decrypt(encrypted))
        if len(plain) < 20:
            raise InvalidCiphertext("decrypted payload is too short")
        content_length = struct.unpack("!I", plain[16:20])[0]
        content_end = 20 + content_length
        if content_end > len(plain):
            raise InvalidCiphertext("declared content length exceeds payload")
        content = plain[20:content_end].decode("utf-8")
        receive_id = plain[content_end:].decode("utf-8")
    except WeComCryptoError:
        raise
    except Exception as exc:
        raise InvalidCiphertext("unable to decrypt callback payload") from exc

    if receive_id != expected_receive_id:
        raise ReceiveIdMismatch("callback receive-id mismatch")
    return DecryptedPayload(content=content, receive_id=receive_id)
