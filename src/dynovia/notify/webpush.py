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

from dynovia.config import PUSH_SUBSCRIPTION, VAPID_PRIVATE_KEY

log = logging.getLogger(__name__)

# Who to contact about a misbehaving push. Required by the spec, never used.
CLAIMS = {"sub": "mailto:dynovia-alerts@example.invalid"}

EXPIRED = (404, 410)
"""The push service says this subscription is dead. iOS does this on its own
after a while, so it is a thing to report, not a bug to fix."""


def configured() -> bool:
    return bool(PUSH_SUBSCRIPTION and VAPID_PRIVATE_KEY)


def send(text: str) -> str | None:
    """Push one message. Returns a message for the owner if the subscription
    has expired, otherwise None. Never raises: a broken push must not stop a
    Telegram notification that already went out."""
    if not configured():
        return None

    try:
        from pywebpush import WebPushException, webpush

        title, _, body = text.partition("\n")
        webpush(
            subscription_info=json.loads(PUSH_SUBSCRIPTION),
            data=json.dumps({"title": title.strip(), "body": body.strip()}),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=dict(CLAIMS),
        )
        return None
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        if status in EXPIRED:
            log.warning("push subscription expired (%s)", status)
            return (
                "🔕 Subskrypcja push wygasła - otwórz aplikację z ekranu głównego, "
                "zakładka Źródła, i włącz powiadomienia jeszcze raz."
            )
        log.exception("push failed (%s)", status)
        return None
    except Exception:  # noqa: BLE001
        log.exception("push failed")
        return None
