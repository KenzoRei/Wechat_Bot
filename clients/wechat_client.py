import json
import requests
from urllib.parse import urlparse, parse_qs


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


def _upload_media_to_group_webhook(webhook_url: str, file_bytes: bytes, filename: str) -> str | None:
    """
    Uploads a file to the Group Robot Webhook's media store, returning a
    media_id (valid 3 days, single-webhook-scoped — never cache/reuse across
    requests, always upload fresh). Uses the same `key` query param already
    embedded in webhook_url — no separate credentials needed.
    """
    key = parse_qs(urlparse(webhook_url).query).get("key", [None])[0]
    if not key:
        print(f"[wechat_client] group_robot_webhook_url missing key param — cannot upload media", flush=True)
        return None

    upload_url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/upload_media?key={key}&type=file"
    try:
        resp = requests.post(upload_url, files={"media": (filename, file_bytes)}, timeout=15)
        data = resp.json()
        if data.get("errcode") != 0:
            print(f"[wechat_client] media upload failed: {data}", flush=True)
            return None
        return data.get("media_id")
    except (requests.RequestException, ValueError) as e:
        print(f"[wechat_client] media upload request failed: {e}", flush=True)
        return None


def send_group_webhook_file(webhook_url: str, file_bytes: bytes, filename: str) -> None:
    """
    Sends a file via Group Robot Webhook (upload, then reference the
    resulting media_id). response_url (the private per-message reply
    channel) does NOT support file messages at all — confirmed against the
    official passive-reply doc — so file delivery is only possible via this
    channel, meaning it always goes to the whole group, never privately to
    one requester. Silently no-ops if webhook_url isn't configured, same
    pattern as send_group_webhook_message.
    """
    if not webhook_url:
        print(f"[wechat_client] No group_robot_webhook_url — cannot send file", flush=True)
        return

    media_id = _upload_media_to_group_webhook(webhook_url, file_bytes, filename)
    if not media_id:
        return

    payload = {"msgtype": "file", "file": {"media_id": media_id}}
    try:
        resp = requests.post(
            webhook_url, json=payload,
            headers={"Content-Type": "application/json"}, timeout=10
        )
        print(f"[wechat_client] file send status={resp.status_code} body={resp.text[:150]}", flush=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[wechat_client] file send failed: {e}", flush=True)
