import json
import requests

def send_message(wechat_openid: str, content: str, response_url: str = "") -> None:
    """
    Sends a reply via Smart Robot response_url.

    response_url is included in each incoming webhook message.
    Active replies use plain JSON (no encryption) with msgtype "text".
    Encryption is only for passive replies (within the webhook response body).
    """
    if not response_url:
        print(f"[wechat_client] No response_url — cannot send reply", flush=True)
        return

    print(f"[wechat_client] Sending to response_url: {content[:40]}", flush=True)

    payload = {
        "msgtype": "text",
        "text": {
            "content": content
        }
    }

    try:
        resp = requests.post(
            response_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        print(f"[wechat_client] status={resp.status_code} body={resp.text[:100]}", flush=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[wechat_client] POST failed: {e}", flush=True)
        raise RuntimeError(f"send_message to response_url failed: {e}")
