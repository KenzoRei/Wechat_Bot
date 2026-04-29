from wechatpy.enterprise.crypto import WeChatCrypto
import xml.etree.ElementTree as ET
import hashlib
import config

# Lazy initialization — crypto object is created on first use, not at import time.
# This allows the server to start even when WECHAT_ENCODING_AES_KEY is a placeholder.
_crypto = None

def _get_crypto() -> WeChatCrypto:
    global _crypto
    if _crypto is None:
        _crypto = WeChatCrypto(
            token=config.WECHAT_TOKEN,
            encoding_aes_key=config.WECHAT_ENCODING_AES_KEY,
            corp_id=config.WECHAT_CORP_ID
        )
    return _crypto


def validate_signature(msg_signature: str, timestamp: str, nonce: str, echo_str: str = "") -> bool:
    """Validates WeChat's SHA1 signature."""
    params = sorted([config.WECHAT_TOKEN, timestamp, nonce, echo_str])
    expected = hashlib.sha1("".join(params).encode("utf-8")).hexdigest()
    return expected == msg_signature


def handle_get_webhook(msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
    """
    Handles GET /webhook (WeChat URL verification).
    In safe mode (AES encryption), echostr is encrypted.
    check_signature() validates the signature AND decrypts echostr in one call.
    Returns the decrypted plain echostr — WeChat expects this exact string back.
    """
    try:
        plain_echostr = _get_crypto().check_signature(msg_signature, timestamp, nonce, echostr)
        return plain_echostr
    except Exception as e:
        raise ValueError(f"Signature check or decryption failed: {e}")


def handle_post_webhook(
    xml_body: str,
    msg_signature: str,
    timestamp: str,
    nonce: str
) -> dict:
    """
    Handles POST /webhook (incoming message).
    Validates signature, decrypts body, extracts message fields.
    Raises ValueError if signature is invalid.
    """
    if not validate_signature(msg_signature, timestamp, nonce):
        raise ValueError("Invalid signature")

    decrypted = _get_crypto().decrypt_message(xml_body, msg_signature, timestamp, nonce)
    return _extract_message(decrypted)


def _extract_message(decrypted_xml: str) -> dict:
    """Parses decrypted XML and returns a structured message dict."""
    root = ET.fromstring(decrypted_xml)
    return {
        "from_user":   root.findtext("FromUserName"),
        "group_id":    root.findtext("ToUserName"),
        "msg_type":    root.findtext("MsgType"),
        "content":     root.findtext("Content", ""),
        "msg_id":      root.findtext("MsgId"),
        "agent_id":    root.findtext("AgentID"),
        "create_time": root.findtext("CreateTime"),
    }
