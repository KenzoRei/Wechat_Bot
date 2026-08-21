import os
from dotenv import load_dotenv

load_dotenv()  # loads .env file into os.environ at startup


def _require(name: str) -> str:
    """Raise at startup if a required env var is missing."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _require_bool(name: str, default: str) -> bool:
    """
    Parse deployment-mode flags strictly. A permissive comparison would turn
    any typo
    ("tru", "yes", "TRUE ") into false with no warning -- exactly the wrong
    failure mode for a flag that controls which channel's credentials are
    even required. Only "true"/"false" (case-insensitive, whitespace-
    trimmed) are accepted; anything else fails startup immediately.
    """
    raw = os.getenv(name, default).strip().lower()
    if raw not in ("true", "false"):
        raise RuntimeError(
            f"Invalid value for {name}: {os.getenv(name, default)!r} -- must be exactly 'true' or 'false'"
        )
    return raw == "true"


# Deployment modes are explicit booleans, never inferred from which
# credentials happen to be present --
# that would make a partially-configured channel silently disabled instead
# of failing fast. SMART_ROBOT_ENABLED defaults true (matches every
# existing deployment's actual behavior before this flag existed --
# nothing regresses for a deployment that never sets it). KEFU_ENABLED
# controls business processing (clients, workers, jobs).
#
# KEFU_CALLBACK_ENABLED is separate from KEFU_ENABLED. Gating only processing
# would leave a
# Smart-Bot-only deployment still requiring Kefu callback credentials for a
# route it would never use). Defaults true, matching every deployment's
# behavior before this flag existed -- WeCom's callback URL must verify
# before it will issue the Kefu API Secret, so leaving this on by default
# preserves that bootstrap path. Set to false only for a deployment that
# will never touch Kefu at all.
SMART_ROBOT_ENABLED   = _require_bool("SMART_ROBOT_ENABLED", "true")
KEFU_ENABLED          = _require_bool("KEFU_ENABLED", "false")
KEFU_CALLBACK_ENABLED = _require_bool("KEFU_CALLBACK_ENABLED", "true")

if KEFU_ENABLED and not KEFU_CALLBACK_ENABLED:
    # Kefu processing discovers new messages only via the callback-triggered
    # sync event -- enabling processing with the callback off would silently
    # starve it of new work. Fail fast at startup rather than deploy a
    # deployment that looks healthy but never receives anything.
    raise RuntimeError(
        "KEFU_ENABLED=true requires KEFU_CALLBACK_ENABLED=true (the default) -- "
        "Kefu processing has no other way to discover new inbound messages."
    )

# WECHAT_CORP_ID is a WeChat Work COMPANY-level identifier, shared by both
# channels' API calls (Smart Robot's own 自建应用 registration AND Kefu's
# gettoken corpid= param -- clients/kefu_client.py needs it too, it is not
# Smart-Robot-specific despite living under the same historical "company
# credentials" heading). Required whenever ANY WeChat-Work-touching mode is
# active. Requiring it unconditionally would block an admin/health-only
# deployment with every channel flag off.
if SMART_ROBOT_ENABLED or KEFU_CALLBACK_ENABLED or KEFU_ENABLED:
    WECHAT_CORP_ID = _require("WECHAT_CORP_ID")
else:
    WECHAT_CORP_ID = os.getenv("WECHAT_CORP_ID")

# WeChat Work — Smart Robot credentials (智能机器人, used for webhook + message
# sending). Required (fails fast) only when SMART_ROBOT_ENABLED; when
# disabled, read without _require() so an unconfigured, disabled channel does
# not fail startup. An enabled but incomplete channel must fail fast.
if SMART_ROBOT_ENABLED:
    WECHAT_BOT_TOKEN            = _require("WECHAT_BOT_TOKEN")
    WECHAT_BOT_ENCODING_AES_KEY = _require("WECHAT_BOT_ENCODING_AES_KEY")
else:
    WECHAT_BOT_TOKEN            = os.getenv("WECHAT_BOT_TOKEN")
    WECHAT_BOT_ENCODING_AES_KEY = os.getenv("WECHAT_BOT_ENCODING_AES_KEY")

# WECHAT_SECRET/WECHAT_AGENT_ID/WECHAT_BOT_ID/WECHAT_BOT_SECRET (legacy 自建应用
# credentials) were removed after confirming they had no consumers.
# If Render's dashboard still has these set, that's harmless; nothing reads
# them anymore. Remove them from the deployment environment whenever
# convenient -- not required for correctness.

# WeChat Kefu credentials (微信客服). Callback crypto is required before full
# processing because WeCom verifies
# the URL before it reveals the Kefu API Secret. Required only when
# KEFU_CALLBACK_ENABLED (see above) -- a deployment with both Kefu flags off
# needs neither of these.
if KEFU_CALLBACK_ENABLED:
    WECHAT_KEFU_TOKEN            = _require("WECHAT_KEFU_TOKEN")
    WECHAT_KEFU_ENCODING_AES_KEY = _require("WECHAT_KEFU_ENCODING_AES_KEY")
else:
    WECHAT_KEFU_TOKEN            = os.getenv("WECHAT_KEFU_TOKEN")
    WECHAT_KEFU_ENCODING_AES_KEY = os.getenv("WECHAT_KEFU_ENCODING_AES_KEY")

if KEFU_ENABLED:
    WECHAT_KEFU_SECRET            = _require("WECHAT_KEFU_SECRET")
    WECHAT_KEFU_OPEN_KFID         = _require("WECHAT_KEFU_OPEN_KFID")
    # One open_kfid maps to exactly one group_id for the single U-Choice
    # tenant. The mapping is fixed at deployment and is never selected by a
    # staff message; core/kefu_registration.py consumes it.
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

# Exactly one deployed process/instance should run the BackgroundScheduler.
# Defaults true so a single-instance deployment (the only supported
# topology today) needs no configuration change. If this ever runs as more
# than one process/replica, every process but one must set this to false --
# there is no leader election, so two schedulers means duplicated jobs.
RUN_SCHEDULER = _require_bool("RUN_SCHEDULER", "true")
