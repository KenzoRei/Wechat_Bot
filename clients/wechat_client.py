"""
WeChat Work Smart Robot message sending.

Instead of using the WeChatClient API (which requires Corp Secret + Agent ID),
Smart Robot messages are sent via response_url — a per-message callback URL
provided by WeChat in each incoming webhook payload.

The response is encrypted JSON in stream format using WXBizJsonMsgCrypt.
"""
import json
import random
import string
import requests
import config
from core.WXBizJsonMsgCrypt import WXBizJsonMsgCrypt

_RECEIVE_ID = ''   # Smart Robot always uses empty receiveid


def send_message(wechat_openid: str, content: str, response_url: str = "") -> None:
    """
    Sends a plain text message back to a user via Smart Robot response_url.

    response_url is included in each incoming webhook message and allows
    the server to reply even after the initial 5-second window.

    Falls back silently if response_url is not available.
    Raises RuntimeError if the HTTP call fails.
    """
    if not response_url:
        print(f"[wechat_client] No response_url available for {wechat_openid}, skipping send")
        return

    stream_id = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
    payload = json.dumps({
        "msgtype": "stream",
        "stream": {
            "id":      stream_id,
            "finish":  True,
            "content": content
        }
    }, ensure_ascii=False)

    crypt = WXBizJsonMsgCrypt(
        config.WECHAT_TOKEN,
        config.WECHAT_ENCODING_AES_KEY,
        _RECEIVE_ID
    )

    nonce     = ''.join(random.choices(string.digits, k=10))
    timestamp = str(int(__import__('time').time()))

    ret, encrypted = crypt.EncryptMsg(payload, nonce, timestamp)
    if ret != 0:
        raise RuntimeError(f"EncryptMsg failed, error code: {ret}")

    try:
        resp = requests.post(
            response_url,
            data=encrypted,
            headers={"Content-Type": "text/plain"},
            timeout=10
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"send_message to response_url failed: {e}")
