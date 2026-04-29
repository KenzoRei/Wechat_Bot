import os
from dotenv import load_dotenv

load_dotenv()  # loads .env file into os.environ at startup


def _require(name: str) -> str:
    """Raise at startup if a required env var is missing."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


# WeChat Work
WECHAT_CORP_ID          = _require("WECHAT_CORP_ID")
WECHAT_SECRET           = _require("WECHAT_SECRET")
WECHAT_AGENT_ID         = _require("WECHAT_AGENT_ID")
WECHAT_TOKEN            = _require("WECHAT_TOKEN")
WECHAT_ENCODING_AES_KEY = _require("WECHAT_ENCODING_AES_KEY")

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
