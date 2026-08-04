import json
import requests


def _post_markdown(url: str, content: str) -> None:
    """Shared POST logic — identical payload shape for response_url and Group Robot Webhook."""
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "content": content
        }
    }
    try:
        resp = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        print(f"[wechat_client] status={resp.status_code} body={resp.text[:150]}", flush=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[wechat_client] POST failed: {e}", flush=True)
        raise RuntimeError(f"POST to {url} failed: {e}")


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
    _post_markdown(response_url, content)


def send_group_webhook_message(webhook_url: str, content: str) -> None:
    """
    Sends a message via WeChat Work Group Robot Webhook (群机器人) — a
    persistent, static per-group URL, unlike the single-use response_url.
    Used for scheduled/proactive pushes: daily broadcast, monthly invoice,
    cross-group completion notifications. Same payload shape as send_message,
    just a different (reusable) target URL.

    @mention supported via `<@userid>` inline in content (not on markdown_v2,
    which this function doesn't use). Rate limit: 20 messages/minute per robot.
    """
    if not webhook_url:
        print(f"[wechat_client] No group_robot_webhook_url — cannot send group message", flush=True)
        return

    print(f"[wechat_client] Sending markdown to group webhook: {content[:40]}", flush=True)
    _post_markdown(webhook_url, content)
