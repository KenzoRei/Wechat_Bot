import json
import requests


def send_message(wechat_openid: str, content: str, response_url: str = "") -> None:
    """
    Sends a reply via Smart Robot response_url.

    response_url only supports msgtype "markdown" and "template_card".
    No encryption needed — the URL itself is the authentication token.
    Valid for 1 hour, single use per URL.
    """
    if not response_url:
        print(f"[wechat_client] No response_url — cannot send reply", flush=True)
        return

    print(f"[wechat_client] Sending markdown to response_url: {content[:40]}", flush=True)

    payload = {
        "msgtype": "markdown",
        "markdown": {
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
        print(f"[wechat_client] status={resp.status_code} body={resp.text[:150]}", flush=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[wechat_client] POST failed: {e}", flush=True)
        raise RuntimeError(f"send_message to response_url failed: {e}")
