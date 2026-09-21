"""Configuration, read from the environment.

Locally the values come from a .env file, in GitHub Actions from repository
Secrets. No python-dotenv: in Actions there is no .env at all, so the loader is
a five-line developer convenience rather than a dependency.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path | None = None) -> None:
    path = path or ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")

DATA_DIR = ROOT / "data"
CACHE_DIR = ROOT / ".cache"
FIXTURES_DIR = ROOT / "tests" / "fixtures"
DB_PATH = DATA_DIR / "dynovia.db"
ROSTER_PATH = ROOT / "kadra.txt"
PROTOCOLS_DIR = ROOT / "protokoly"
SCHEDULE_PATH = PROTOCOLS_DIR / "terminarz.txt"
