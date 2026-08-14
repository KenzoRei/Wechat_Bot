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
    Strict boolean parsing for deployment-mode flags (Codex round-3 review):
    the previous `os.getenv(...).lower() == "true"` silently turned any typo
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


# kefu-migration-plan.md Sec 6.4 (Codex round-68 finding 5): explicit
# booleans, never derived from which credentials happen to be present --
# that would make a partially-configured channel silently disabled instead
# of failing fast. SMART_ROBOT_ENABLED defaults true (matches every
# existing deployment's actual behavior before this flag existed --
# nothing regresses for a deployment that never sets it). KEFU_ENABLED
# controls business processing (clients, workers, jobs).
#
# KEFU_CALLBACK_ENABLED is a separate mode from KEFU_ENABLED (signed
# cross-review plan, Section C1 follow-up -- Codex's round-2 review of this
# same plan correctly flagged that only gating processing left a
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
# active (Codex round-3 review: this was unconditional, which would still
# have blocked a genuine admin/health-only deployment with every channel
# flag off). Read without _require() only when nothing needs it.
if SMART_ROBOT_ENABLED or KEFU_CALLBACK_ENABLED or KEFU_ENABLED:
    WECHAT_CORP_ID = _require("WECHAT_CORP_ID")
else:
    WECHAT_CORP_ID = os.getenv("WECHAT_CORP_ID")

# WeChat Work — Smart Robot credentials (智能机器人, used for webhook + message
# sending). Required (fails fast) only when SMART_ROBOT_ENABLED; when
# disabled, read without _require() so an unconfigured, disabled channel can
# never fail startup -- per Sec 6.4, an enabled-but-incomplete channel
# always must. Codex round-88 finding 3: these were previously required
# unconditionally, which blocked a genuinely Kefu-only deployment.
if SMART_ROBOT_ENABLED:
    WECHAT_TOKEN            = _require("WECHAT_TOKEN")
    WECHAT_ENCODING_AES_KEY = _require("WECHAT_ENCODING_AES_KEY")
else:
    WECHAT_TOKEN            = os.getenv("WECHAT_TOKEN")
    WECHAT_ENCODING_AES_KEY = os.getenv("WECHAT_ENCODING_AES_KEY")

# WECHAT_SECRET/WECHAT_AGENT_ID/WECHAT_BOT_ID/WECHAT_BOT_SECRET (legacy 自建应用
# credentials) were removed here (signed cross-review plan, Section C4) --
# confirmed zero references anywhere in this repository outside this file.
# If Render's dashboard still has these set, that's harmless; nothing reads
# them anymore. Remove them from the deployment environment whenever
# convenient -- not required for correctness.

# WeChat Kefu credentials (微信客服) -- Sec 6.1's config list; consumed by
# Callback crypto is required before full processing because WeCom verifies
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

# Scheduler ownership (signed cross-review plan, Section C3 follow-up):
# exactly one deployed process/instance should run the BackgroundScheduler.
# Defaults true so a single-instance deployment (the only supported
# topology today) needs no configuration change. If this ever runs as more
# than one process/replica, every process but one must set this to false --
# there is no leader election, so two schedulers means duplicated jobs.
RUN_SCHEDULER = _require_bool("RUN_SCHEDULER", "true")
