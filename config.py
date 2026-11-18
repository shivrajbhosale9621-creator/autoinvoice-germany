"""
Centralized configuration for the AutoInvoice German application.
Loads API keys, model names, and default paths from environment variables and .env files.
Note: ChromaDB vector search has been replaced with SQLite + FTS5 (Task 1).
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["ANONYMIZED_TELEMETRY"] = "False"

# Legacy path constants kept for backwards-compatible function signatures
DEFAULT_CHROMA_PATH = os.getenv("CHROMA_DB_PATH", "./chroma_db")
DEFAULT_CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "invoices")
DEFAULT_INDEX_PATH = DEFAULT_CHROMA_PATH
DEFAULT_INBOX_DIR = os.getenv("INBOX_DIR", "./invoices_to_process")

DEFAULT_GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

_SECRETS_LOCATIONS = [
    Path.home() / ".streamlit" / "secrets.toml",
    Path.cwd() / ".streamlit" / "secrets.toml",
]
HAS_STREAMLIT_SECRETS = any(path.exists() for path in _SECRETS_LOCATIONS)


def _get_secret(key: str, default: str = "") -> str:
    if not HAS_STREAMLIT_SECRETS:
        return default
    try:
        import streamlit as st
        return st.secrets[key]
    except (KeyError, Exception):
        return default


GROQ_API_KEY = os.getenv("GROQ_API_KEY") or _get_secret("GROQ_API_KEY", "")
GROQ_MODEL_NAME = os.getenv("GROQ_MODEL_NAME", "openai/gpt-oss-120b")

BAIDU_API_KEY = os.getenv("BAIDU_API_KEY") or _get_secret("BAIDU_API_KEY", "")
BAIDU_SECRET_KEY = os.getenv("BAIDU_SECRET_KEY") or _get_secret("BAIDU_SECRET_KEY", "")
USE_LOCAL_PADDLEOCR = os.getenv("USE_LOCAL_PADDLEOCR", "false").lower() == "true"

SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY") or _get_secret("SILICONFLOW_API_KEY", "")
SILICONFLOW_OCR_MODEL = os.getenv("SILICONFLOW_OCR_MODEL") or _get_secret("SILICONFLOW_OCR_MODEL", "Qwen/Qwen2-VL-72B-Instruct")
PADDLE_OCR_ENABLED = os.getenv("PADDLE_OCR_ENABLED", "true").lower() == "true"

GROQ_VISION_MODEL_NAME = os.getenv("GROQ_VISION_MODEL_NAME", GROQ_MODEL_NAME)

# --- SQLite Database Path (shared across Auth, Invoices, FTS5, Vendors, SEPA, and Chat) ---
CHAT_DB_PATH = os.getenv(
    "CHAT_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "invoices.db"),
)

try:
    SUMMARISE_THRESHOLD = int(os.getenv("SUMMARISE_THRESHOLD", "20"))
except ValueError:
    SUMMARISE_THRESHOLD = 20

try:
    RECENT_WINDOW = int(os.getenv("RECENT_WINDOW", "6"))
except ValueError:
    RECENT_WINDOW = 6
