import json
import random
import string
import requests
import config
from core.WXBizJsonMsgCrypt import WXBizJsonMsgCrypt

_RECEIVE_ID = ''


def send_message(wechat_openid: str, content: str, response_url: str = "") -> None:
    """
    Sends a reply via Smart Robot response_url.
    Tries encrypted text format — same encryption as passive reply but msgtype "text".
    """
    if not response_url:
        print(f"[wechat_client] No response_url — cannot send reply", flush=True)
        return

    print(f"[wechat_client] Sending to response_url: {content[:40]}", flush=True)

    payload = json.dumps({
        "msgtype": "text",
        "text": {"content": content}
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
        print(f"[wechat_client] EncryptMsg failed ret={ret}", flush=True)
        return

    try:
        resp = requests.post(
            response_url,
            data=encrypted,
            headers={"Content-Type": "text/plain"},
            timeout=10
        )
        print(f"[wechat_client] status={resp.status_code} body={resp.text[:150]}", flush=True)
    except requests.RequestException as e:
        print(f"[wechat_client] POST failed: {e}", flush=True)
        raise RuntimeError(f"send_message to response_url failed: {e}")
