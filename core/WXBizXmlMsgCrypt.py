"""Strict XML envelope adapter for WeChat Kefu callbacks."""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from core import ierror
from core.wecom_crypto import (
    InvalidCiphertext,
    InvalidEncodingKey,
    InvalidSignature,
    ReceiveIdMismatch,
    compute_signature,
    decode_encoding_aes_key,
    decrypt_payload,
    encrypt_payload,
    verify_signature,
)


MAX_XML_BYTES = 256 * 1024


class XmlEnvelopeError(ValueError):
    pass


def _extract_encrypt(xml_text: str | bytes) -> str:
    raw = xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text
    if len(raw) > MAX_XML_BYTES:
        raise XmlEnvelopeError("XML envelope is too large")
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise XmlEnvelopeError("DTD and entity declarations are forbidden")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise XmlEnvelopeError("malformed XML envelope") from exc
    if root.tag != "xml":
        raise XmlEnvelopeError("unexpected XML root")
    allowed = {"Encrypt", "ToUserName", "AgentID"}
    if any(child.tag not in allowed for child in root):
        raise XmlEnvelopeError("unexpected XML field")
    nodes = root.findall("Encrypt")
    if len(nodes) != 1 or not nodes[0].text:
        raise XmlEnvelopeError("XML envelope must contain one Encrypt field")
    return nodes[0].text.strip()


class WXBizXmlMsgCrypt:
    """Tencent-compatible return-code wrapper around strict primitives."""

    def __init__(self, token: str, encoding_aes_key: str, receive_id: str):
        self.token = token
        self.receive_id = receive_id
        try:
            self.key = decode_encoding_aes_key(encoding_aes_key)
        except InvalidEncodingKey as exc:
            raise ValueError("invalid EncodingAESKey") from exc

    def VerifyURL(self, msg_signature, timestamp, nonce, echostr):
        try:
            verify_signature(self.token, timestamp, nonce, echostr, msg_signature)
            result = decrypt_payload(echostr, self.receive_id, self.key)
            return ierror.WXBizMsgCrypt_OK, result.content
        except InvalidSignature:
            return ierror.WXBizMsgCrypt_ValidateSignature_Error, None
        except ReceiveIdMismatch:
            return ierror.WXBizMsgCrypt_ValidateCorpid_Error, None
        except InvalidCiphertext:
            return ierror.WXBizMsgCrypt_DecryptAES_Error, None

    def DecryptMsg(self, post_data, msg_signature, timestamp, nonce):
        try:
            encrypted = _extract_encrypt(post_data)
            verify_signature(self.token, timestamp, nonce, encrypted, msg_signature)
            result = decrypt_payload(encrypted, self.receive_id, self.key)
            return ierror.WXBizMsgCrypt_OK, result.content
        except XmlEnvelopeError:
            return ierror.WXBizMsgCrypt_ParseXml_Error, None
        except InvalidSignature:
            return ierror.WXBizMsgCrypt_ValidateSignature_Error, None
        except ReceiveIdMismatch:
            return ierror.WXBizMsgCrypt_ValidateCorpid_Error, None
        except InvalidCiphertext:
            return ierror.WXBizMsgCrypt_DecryptAES_Error, None

    def EncryptMsg(self, reply_msg, nonce, timestamp=None):
        timestamp = str(int(time.time())) if timestamp is None else str(timestamp)
        encrypted = encrypt_payload(reply_msg, self.receive_id, self.key)
        signature = compute_signature(self.token, timestamp, nonce, encrypted)
        xml = (
            "<xml>"
            f"<Encrypt><![CDATA[{encrypted}]]></Encrypt>"
            f"<MsgSignature><![CDATA[{signature}]]></MsgSignature>"
            f"<TimeStamp>{escape(timestamp)}</TimeStamp>"
            f"<Nonce><![CDATA[{escape(str(nonce))}]]></Nonce>"
            "</xml>"
        )
        return ierror.WXBizMsgCrypt_OK, xml
