"""Telegram delivery and polling.

Notifications go out as plain text - scraped team names land in them and plain
text has nothing to escape. Command replies use HTML, because tables only line
up inside <pre>, and those are built here where the escaping is visible.

There is no webhook and no server: updates are fetched during the cron run, so
a command is answered on the next tick rather than instantly.
"""

from __future__ import annotations

import html
import logging

import httpx

from dynovia.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"


def _call(method: str, payload: dict) -> dict:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing - set them in .env "
            "locally or in GitHub Secrets"
        )
    response = httpx.post(
        API.format(token=TELEGRAM_BOT_TOKEN, method=method), json=payload, timeout=30.0
    )
    response.raise_for_status()
    return response.json()


def send(
    text: str,
    *,
    buttons: list[list[tuple[str, str]]] | None = None,
    as_html: bool = False,
    dry_run: bool = False,
) -> None:
    """buttons are [[(label, callback_data), ...], ...] - one list per row."""
    if dry_run:
        print(f"[dry-run] {text}")
        return
    payload: dict = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    if as_html:
        payload["parse_mode"] = "HTML"
    if buttons:
        payload["reply_markup"] = {
            "inline_keyboard": [
                [{"text": label, "callback_data": data} for label, data in row]
                for row in buttons
            ]
        }
    _call("sendMessage", payload)
    log.info("sent: %s", text.splitlines()[0][:70])


def get_updates(offset: int) -> list[dict]:
    """Commands sent since the last run. timeout=0 because this is a cron tick,
    not a daemon: whatever is waiting is answered now."""
    result = _call("getUpdates", {"offset": offset, "timeout": 0, "limit": 20})
    return result.get("result", [])


def answer_callback(callback_id: str, text: str = "") -> None:
    """Stops the button spinning. Telegram shows the text as a small toast."""
    _call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


def pre(text: str) -> str:
    """A monospaced block. Tables are the only thing that needs one, and the
    contents are scraped team names, so they are escaped."""
    return f"<pre>{html.escape(text)}</pre>"
