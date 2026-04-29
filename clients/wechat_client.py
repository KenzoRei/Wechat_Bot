from wechatpy.enterprise import WeChatClient
import config

# Smart Robot uses Bot Secret (not App Secret) to authenticate.
# Bot ID is used as the agent_id when sending messages.
_client = None

def _get_client() -> WeChatClient:
    global _client
    if _client is None:
        _client = WeChatClient(
            corp_id=config.WECHAT_CORP_ID,
            secret=config.WECHAT_BOT_SECRET   # Bot Secret, not App Secret
        )
    return _client


def send_message(wechat_openid: str, content: str) -> None:
    """
    Sends a plain text message to a user via WeChat Work API.
    Uses Smart Robot credentials (Bot ID as agent_id).
    Raises RuntimeError if the API call fails.
    """
    try:
        _get_client().message.send_text(
            agent_id=config.WECHAT_BOT_ID,    # Bot ID, not App Agent ID
            to_user=[wechat_openid],
            content=content
        )
    except Exception as e:
        raise RuntimeError(f"WeChat send_message failed for {wechat_openid}: {e}")
