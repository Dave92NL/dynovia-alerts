"""Web push to the phone's home screen icon.

Deliberately the second channel, not the first. iOS only delivers push to a
page that was added to the home screen, quietly drops the subscription after a
few weeks of not opening it, and can be minutes late. Telegram is the one that
has to work; this is the one that shows up on the lock screen.

With no subscription configured this does nothing at all, so the rest of the
app runs exactly the same whether push is set up or not.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from dynovia.config import PUSH_SUBSCRIPTION, VAPID_PRIVATE_KEY, VAPID_SUBJECT

log = logging.getLogger(__name__)

# The spec lets this be a mailto: or an https: URL, but py_vapid accepts only
# mailto:, and Apple returns a flat 403 for an unroutable domain. So it has to
# be a real address, and it comes from Secrets - this repository is public.

EXPIRED = (404, 410)
"""The push service says this subscription is dead. iOS does this on its own
after a while, so it is a thing to report, not a bug to fix."""


@dataclass(frozen=True, slots=True)
class Problem:
    message: str
    expired: bool
    """Only an expired subscription is worth interrupting the owner over; the
    rest is for whoever asked with /push."""


def configured() -> bool:
    return bool(PUSH_SUBSCRIPTION and VAPID_PRIVATE_KEY and VAPID_SUBJECT)


def send(text: str) -> Problem | None:
    """Push one message. Returns what went wrong, or None on success. Never
    raises: a broken push must not stop a Telegram notification that already
    went out."""
    if not configured():
        missing = [
            name
            for name, value in (
                ("PUSH_SUBSCRIPTION", PUSH_SUBSCRIPTION),
                ("VAPID_PRIVATE_KEY", VAPID_PRIVATE_KEY),
                ("VAPID_SUBJECT", VAPID_SUBJECT),
            )
            if not value
        ]
        return Problem("Brak w sekretach: " + ", ".join(missing), expired=False)

    try:
        from pywebpush import WebPushException, webpush

        title, _, body = text.partition("\n")
        webpush(
            subscription_info=json.loads(PUSH_SUBSCRIPTION),
            data=json.dumps({"title": title.strip(), "body": body.strip()}),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_SUBJECT},
        )
        return None
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        detail = (getattr(exc.response, "text", "") or "")[:160]
        if status in EXPIRED:
            log.warning("push subscription expired (%s)", status)
            return Problem(
                "🔕 Subskrypcja push wygasła - otwórz aplikację z ekranu głównego, "
                "zakładka Źródła, i włącz powiadomienia jeszcze raz.",
                expired=True,
            )
        log.error("push failed (%s): %s", status, detail)
        return Problem(f"Push odrzucony: HTTP {status}. {detail}".strip(), expired=False)
    except Exception as exc:  # noqa: BLE001
        log.exception("push failed")
        return Problem(f"Push nie wyszedł: {type(exc).__name__}", expired=False)
