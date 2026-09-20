"""Telegram delivery. Plain text, no parse_mode: scraped team names go straight
into these messages and plain text has nothing to escape. Phase 4 adds HTML mode
where it is actually needed, for <pre> tables."""

from __future__ import annotations

import logging

import httpx

from dynovia.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"


def send(text: str, *, dry_run: bool = False) -> None:
    if dry_run:
        print(f"[dry-run] {text}")
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing - set them in .env "
            "locally or in GitHub Secrets"
        )
    response = httpx.post(
        API.format(token=TELEGRAM_BOT_TOKEN),
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
        timeout=20.0,
    )
    response.raise_for_status()
    log.info("sent: %s", text)
