import json
import random
import string
import requests
import config
from core.WXBizJsonMsgCrypt import WXBizJsonMsgCrypt

_RECEIVE_ID = ''


def send_message(wechat_openid: str, content: str, response_url: str = "") -> None:
    if not response_url:
        print(f"[wechat_client] No response_url — cannot send reply to {wechat_openid}", flush=True)
        return

    print(f"[wechat_client] Sending via response_url to {wechat_openid}: {content[:30]}", flush=True)

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
        print(f"[wechat_client] EncryptMsg failed, ret={ret}", flush=True)
        raise RuntimeError(f"EncryptMsg failed, error code: {ret}")

    try:
        resp = requests.post(
            response_url,
            data=encrypted,
            headers={"Content-Type": "text/plain"},
            timeout=10
        )
        print(f"[wechat_client] response_url POST status: {resp.status_code} body: {resp.text[:100]}", flush=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[wechat_client] POST failed: {e}", flush=True)
        raise RuntimeError(f"send_message to response_url failed: {e}")
