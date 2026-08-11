import os
from dotenv import load_dotenv

load_dotenv()  # loads .env file into os.environ at startup


def _require(name: str) -> str:
    """Raise at startup if a required env var is missing."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


# kefu-migration-plan.md Sec 6.4 (Codex round-68 finding 5): explicit
# booleans, never derived from which credentials happen to be present --
# that would make a partially-configured channel silently disabled instead
# of failing fast. SMART_ROBOT_ENABLED defaults true (matches every
# existing deployment's actual behavior before this flag existed --
# nothing regresses for a deployment that never sets it). KEFU_ENABLED
# controls business processing; callback verification stays available.
SMART_ROBOT_ENABLED = os.getenv("SMART_ROBOT_ENABLED", "true").lower() == "true"
KEFU_ENABLED         = os.getenv("KEFU_ENABLED", "false").lower() == "true"

# WECHAT_CORP_ID is a WeChat Work COMPANY-level identifier, shared by both
# channels' API calls (Smart Robot's own 自建应用 registration AND Kefu's
# gettoken corpid= param -- clients/kefu_client.py needs it too, it is not
# Smart-Robot-specific despite living under the same historical "company
# credentials" heading). Required whenever EITHER channel is enabled.
WECHAT_CORP_ID = _require("WECHAT_CORP_ID")

# WeChat Work — Smart Robot credentials (智能机器人, used for webhook + message
# sending), plus WECHAT_SECRET/WECHAT_AGENT_ID (legacy 自建应用 credentials,
# kept for reference -- confirmed unused by any code path). Required (fails
# fast) only when SMART_ROBOT_ENABLED; when disabled, read without
# _require() so an unconfigured, disabled channel can never fail startup --
# per Sec 6.4, an enabled-but-incomplete channel always must. Codex
# round-88 finding 3: these were previously required unconditionally,
# which blocked a genuinely Kefu-only deployment.
if SMART_ROBOT_ENABLED:
    WECHAT_SECRET           = _require("WECHAT_SECRET")
    WECHAT_AGENT_ID         = _require("WECHAT_AGENT_ID")
    WECHAT_TOKEN            = _require("WECHAT_TOKEN")
    WECHAT_ENCODING_AES_KEY = _require("WECHAT_ENCODING_AES_KEY")
    WECHAT_BOT_ID           = _require("WECHAT_BOT_ID")     # Bot ID from Smart Robot page
    WECHAT_BOT_SECRET       = _require("WECHAT_BOT_SECRET") # Secret from Smart Robot page
else:
    WECHAT_SECRET           = os.getenv("WECHAT_SECRET")
    WECHAT_AGENT_ID         = os.getenv("WECHAT_AGENT_ID")
    WECHAT_TOKEN            = os.getenv("WECHAT_TOKEN")
    WECHAT_ENCODING_AES_KEY = os.getenv("WECHAT_ENCODING_AES_KEY")
    WECHAT_BOT_ID           = os.getenv("WECHAT_BOT_ID")
    WECHAT_BOT_SECRET       = os.getenv("WECHAT_BOT_SECRET")

# WeChat Kefu credentials (微信客服) -- Sec 6.1's config list; consumed by
# Callback crypto is required before full processing because WeCom verifies
# the URL before it reveals the Kefu API Secret.
WECHAT_KEFU_TOKEN            = _require("WECHAT_KEFU_TOKEN")
WECHAT_KEFU_ENCODING_AES_KEY = _require("WECHAT_KEFU_ENCODING_AES_KEY")

if KEFU_ENABLED:
    WECHAT_KEFU_SECRET            = _require("WECHAT_KEFU_SECRET")
    WECHAT_KEFU_OPEN_KFID         = _require("WECHAT_KEFU_OPEN_KFID")
    # kefu-migration-plan.md Sec 2.3: "one open_kfid maps to exactly one
    # group_id (the single U-Choice tenant), fixed at deployment
    # configuration time, never chosen by staff or inferred from a
    # message." Codex round-88 finding 1: this mapping had no config seam
    # yet -- this is it, consumed by core/kefu_registration.py.
    KEFU_GROUP_ID                 = _require("KEFU_GROUP_ID")
else:
    WECHAT_KEFU_SECRET            = os.getenv("WECHAT_KEFU_SECRET")
    WECHAT_KEFU_OPEN_KFID         = os.getenv("WECHAT_KEFU_OPEN_KFID")
    KEFU_GROUP_ID                 = os.getenv("KEFU_GROUP_ID")

# External APIs — base URLs are global; API keys are per-group in group_service.config
YIDIDA_BASE_URL = _require("YIDIDA_BASE_URL")
OMS_BASE_URL    = _require("OMS_BASE_URL")

# Claude AI
CLAUDE_API_KEY = _require("CLAUDE_API_KEY")
CLAUDE_MODEL   = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

# OpenAI
OPENAI_API_KEY = _require("OPENAI_API_KEY")
OPENAI_MODEL   = os.getenv("OPENAI_MODEL", "gpt-4o")

# Admin
ADMIN_API_KEY = _require("ADMIN_API_KEY")

# Database
DATABASE_URL = _require("DATABASE_URL")

# Session
SESSION_EXPIRY_MINUTES = int(os.getenv("SESSION_EXPIRY_MINUTES", "60"))

# Server base URL — used for label download links sent via WeChat
SERVER_BASE_URL = os.getenv("SERVER_BASE_URL", "https://wechat-bot-atse.onrender.com")
