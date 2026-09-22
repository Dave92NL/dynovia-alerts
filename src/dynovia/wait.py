"""The pause between ticks, spent listening instead of sleeping.

    py -3.12 -m dynovia.wait <seconds>

`sleep 600` is deaf: a protocol sent from the phone sits in Telegram's queue
until the next tick happens to look, so the app could be ten minutes behind
data that had already arrived. Long polling holds a connection open instead
and returns the moment anything is sent, which turns those ten minutes into
about one second.

Nothing is consumed here. The offset is not advanced, so whatever arrives is
still waiting for bot.poll on the tick this wakes up - this only decides when
that tick happens.
"""

from __future__ import annotations

import logging
import sys
import time

from dynovia import db
from dynovia.bot import OFFSET_KEY
from dynovia.notify import telegram

log = logging.getLogger("dynovia.wait")

SLICE = 50
"""Seconds per long poll. Telegram caps its own timeout around here, and a
shorter slice just means more round trips for the same waiting."""


def wait_for_message(seconds: int) -> bool:
    """True if something arrived, False if the time ran out."""
    conn = db.connect()
    offset = int(db.get_setting(conn, OFFSET_KEY) or 0)
    conn.close()

    deadline = time.monotonic() + seconds
    while (left := deadline - time.monotonic()) > 0:
        try:
            if telegram.get_updates(offset, wait=int(min(SLICE, left))):
                log.info("cos przyszlo - tik teraz, nie za %ds", int(left))
                return True
        except Exception:  # noqa: BLE001 - a deaf wait still has to end on time
            log.exception("long polling padlo, czekam dalej")
            time.sleep(min(SLICE, max(left, 0)))
    return False


def main(argv: list[str]) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    wait_for_message(int(argv[0]) if argv else 600)


if __name__ == "__main__":
    main(sys.argv[1:])
