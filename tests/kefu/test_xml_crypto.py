import base64

import pytest

from core import ierror
from core.WXBizXmlMsgCrypt import WXBizXmlMsgCrypt
from core.wecom_crypto import compute_signature, decode_encoding_aes_key, encrypt_payload


TOKEN = "test-token"
RECEIVE_ID = "ww1234567890"
KEY_BYTES = bytes(range(32))
ENCODING_KEY = base64.b64encode(KEY_BYTES).decode("ascii").rstrip("=")


def _envelope(content: str, timestamp="1700000000", nonce="n-1"):
    encrypted = encrypt_payload(content, RECEIVE_ID, KEY_BYTES)
    signature = compute_signature(TOKEN, timestamp, nonce, encrypted)
    xml = f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>"
    return xml, signature, timestamp, nonce


def test_round_trip_xml_callback():
    crypt = WXBizXmlMsgCrypt(TOKEN, ENCODING_KEY, RECEIVE_ID)
    xml, signature, timestamp, nonce = _envelope("<xml><Event>kf_msg_or_event</Event></xml>")
    code, plaintext = crypt.DecryptMsg(xml, signature, timestamp, nonce)
    assert code == ierror.WXBizMsgCrypt_OK
    assert plaintext == "<xml><Event>kf_msg_or_event</Event></xml>"


def test_verify_url_round_trip():
    crypt = WXBizXmlMsgCrypt(TOKEN, ENCODING_KEY, RECEIVE_ID)
    encrypted = encrypt_payload("echo-ok", RECEIVE_ID, KEY_BYTES)
    signature = compute_signature(TOKEN, "1", "2", encrypted)
    assert crypt.VerifyURL(signature, "1", "2", encrypted) == (0, "echo-ok")


def test_wrong_signature_is_rejected():
    crypt = WXBizXmlMsgCrypt(TOKEN, ENCODING_KEY, RECEIVE_ID)
    xml, _, timestamp, nonce = _envelope("hello")
    code, plaintext = crypt.DecryptMsg(xml, "0" * 40, timestamp, nonce)
    assert code == ierror.WXBizMsgCrypt_ValidateSignature_Error
    assert plaintext is None


def test_wrong_receive_id_is_rejected():
    crypt = WXBizXmlMsgCrypt(TOKEN, ENCODING_KEY, "different")
    xml, signature, timestamp, nonce = _envelope("hello")
    code, plaintext = crypt.DecryptMsg(xml, signature, timestamp, nonce)
    assert code == ierror.WXBizMsgCrypt_ValidateCorpid_Error
    assert plaintext is None


@pytest.mark.parametrize(
    "xml",
    [
        "<xml>",
        "<root><Encrypt>x</Encrypt></root>",
        "<xml></xml>",
        "<xml><Encrypt>x</Encrypt><Encrypt>y</Encrypt></xml>",
        "<!DOCTYPE xml [<!ENTITY x SYSTEM 'file:///etc/passwd'>]><xml><Encrypt>&x;</Encrypt></xml>",
    ],
)
def test_malformed_missing_and_xxe_envelopes_are_rejected(xml):
    crypt = WXBizXmlMsgCrypt(TOKEN, ENCODING_KEY, RECEIVE_ID)
    code, plaintext = crypt.DecryptMsg(xml, "unused", "1", "2")
    assert code == ierror.WXBizMsgCrypt_ParseXml_Error
    assert plaintext is None


def test_encoding_key_must_decode_to_32_bytes():
    with pytest.raises(ValueError):
        WXBizXmlMsgCrypt(TOKEN, "short", RECEIVE_ID)
    assert decode_encoding_aes_key(ENCODING_KEY) == KEY_BYTES
