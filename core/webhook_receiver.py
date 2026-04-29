"""
WeChat Work Smart Robot (智能机器人) webhook handler.

Key differences from 自建应用:
- receiveid is EMPTY STRING '' (not Corp ID or Bot ID)
- Messages are JSON format (not XML)
- Uses WXBizJsonMsgCrypt (not wechatpy WeChatCrypto)
- Replies must be encrypted JSON (stream format)
"""
import json
import time
import random
import string
import config
from core.WXBizJsonMsgCrypt import WXBizJsonMsgCrypt

# receiveid is always empty string for Smart Robot
_RECEIVE_ID = ''

def _get_crypt() -> WXBizJsonMsgCrypt:
    return WXBizJsonMsgCrypt(
        config.WECHAT_TOKEN,
        config.WECHAT_ENCODING_AES_KEY,
        _RECEIVE_ID
    )


def handle_get_webhook(msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
    """
    Handles GET /webhook — WeChat URL verification for Smart Robot.
    receiveid must be empty string for Smart Robot.
    Returns the decrypted echostr as plain text.
    """
    crypt = _get_crypt()
    ret, plain = crypt.VerifyURL(msg_signature, timestamp, nonce, echostr)
    if ret != 0:
        raise ValueError(f"VerifyURL failed, error code: {ret}")
    return plain


def handle_post_webhook(
    body: bytes,
    msg_signature: str,
    timestamp: str,
    nonce: str
) -> dict:
    """
    Handles POST /webhook — decrypts and parses incoming Smart Robot message.
    Returns a structured message dict for the pipeline.
    Raises ValueError if decryption fails.
    """
    crypt = _get_crypt()
    ret, json_content = crypt.DecryptMsg(body, msg_signature, timestamp, nonce)
    if ret != 0:
        raise ValueError(f"DecryptMsg failed, error code: {ret}")

    data = json.loads(json_content)
    return _extract_message(data)


def _extract_message(data: dict) -> dict:
    """
    Parses the decrypted JSON payload into a structured message dict.

    Smart Robot JSON format (from WeChat Work docs):
    {
        "msgid":        "...",
        "create_time":  1234567890,
        "aibotid":      "aiblPqz...",
        "chatid":       "wrXXX...",          ← group chat ID (top-level)
        "chattype":     "group" | "single",
        "msgtype":      "text",
        "from": {
            "userid":   "user_openid",
            "corpid":   "ww..."
        },
        "text": {"content": "..."},
        "response_url": "https://..."         ← use this to send replies
    }
    """
    msg_type  = data.get("msgtype", "")
    from_info = data.get("from", {})
    chat_type = data.get("chattype", "single")
    group_id  = data.get("chatid") if chat_type == "group" else None

    content = ""
    if msg_type == "text":
        content = data.get("text", {}).get("content", "")
        # Strip @mention prefix — WeChat includes it in content for group messages
        # e.g. "@机器人-测试 5公斤" → "5公斤"
        import re
        content = re.sub(r'^@\S+\s*', '', content).strip()

    return {
        "from_user":    from_info.get("userid", ""),
        "group_id":     group_id,
        "chat_type":    chat_type,
        "msg_type":     msg_type,
        "content":      content,
        "msg_id":       data.get("msgid", ""),
        "response_url": data.get("response_url", ""),  # for sending replies
        "raw":          data,
    }


def make_encrypted_reply(content: str, nonce: str, timestamp: str) -> str:
    """
    Encrypts a text reply in Smart Robot stream format.
    Used to send an immediate acknowledgement back in the POST response.
    """
    stream_id = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
    payload = json.dumps({
        "msgtype": "stream",
        "stream": {
            "id":      stream_id,
            "finish":  True,
            "content": content
        }
    }, ensure_ascii=False)

    crypt = _get_crypt()
    ret, encrypted = crypt.EncryptMsg(payload, nonce, timestamp)
    if ret != 0:
        return ""
    return encrypted
