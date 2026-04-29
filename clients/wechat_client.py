from wechatpy.enterprise import WeChatClient
import config

# Lazy initialization — same reason as webhook_receiver.py.
# Avoids startup failure when credentials are placeholders.
_client = None

def _get_client() -> WeChatClient:
    global _client
    if _client is None:
        _client = WeChatClient(
            corp_id=config.WECHAT_CORP_ID,
            secret=config.WECHAT_SECRET
        )
    return _client


def send_message(wechat_openid: str, content: str) -> None:
    """
    Sends a plain text message to a user via WeChat Work API.
    Raises RuntimeError if the API call fails.
    """
    try:
        _get_client().message.send_text(
            agent_id=config.WECHAT_AGENT_ID,
            to_user=[wechat_openid],
            content=content
        )
    except Exception as e:
        raise RuntimeError(f"WeChat send_message failed for {wechat_openid}: {e}")
