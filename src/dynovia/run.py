"""Entry point for one cron tick.

    py -3.12 -m dynovia.run [--dry-run] [--offline]

--dry-run prints the notifications instead of sending them and records nothing,
so it can be repeated. --offline parses the stored snapshots instead of going
online, which is how this gets exercised during development.
"""

from __future__ import annotations

import datetime as dt
import logging
import sys

from dynovia import db, differ
from dynovia.differ import WARSAW, kickoff
from dynovia.notify import telegram
from dynovia.scrapers import SCRAPERS
from dynovia.snapshot import load_fixtures

log = logging.getLogger("dynovia.run")

MATCH_WINDOW = dt.timedelta(hours=3)  # whistle to final whistle, generously
AFTERMATH = dt.timedelta(hours=72)  # lineups and protocols trickle in this long


def poll_interval(matches, now: dt.datetime) -> dt.timedelta:
    """How often this source deserves to be polled right now.

    The schedule from the plan: every 10 minutes while a match is on, every two
    hours for three days afterwards, otherwise once a day. Without it a */10
    cron would hit these small community-run sites 144 times a day for nothing.
    """
    interval = dt.timedelta(days=1)
    for match in matches:
        start = kickoff(match)
        if start is None:
            continue
        if start <= now <= start + MATCH_WINDOW:
            return dt.timedelta(minutes=10)  # nothing beats a live match
        if start < now <= start + AFTERMATH:
            interval = min(interval, dt.timedelta(hours=2))
    return interval


def is_due(last: dt.datetime | None, matches, now: dt.datetime) -> bool:
    if last is None:
        return True
    interval = poll_interval(matches, now)
    if interval < dt.timedelta(days=1):
        return now - last >= interval
    # The daily refresh is anchored at 06:00 Warsaw time so the "tomorrow"
    # reminders are built from data fetched that morning. Actions runs on UTC,
    # hence the explicit conversion.
    local, last_local = now.astimezone(WARSAW), last.astimezone(WARSAW)
    return local.hour >= 6 and last_local.date() < local.date()


def collect(conn, now: dt.datetime, *, offline: bool) -> list[differ.Event]:
    events: list[differ.Event] = []
    for name, scraper_cls in SCRAPERS.items():
        known = db.stored_matches(conn)
        try:
            if offline:
                result = scraper_cls().parse(load_fixtures(name))
            elif not is_due(db.last_fetch(conn, name), known.values(), now):
                log.info("%s: not due yet", name)
                continue
            else:
                result = scraper_cls().fetch()
        except Exception:  # noqa: BLE001 - one dead source must not kill the run
            log.exception("%s: scrape failed", name)
            continue
        log.info("%s: %d matches", name, len(result.matches))
        events += differ.diff(known, result.matches, now)
        db.store_matches(conn, result.matches, result.source, result.fetched_at)
    return events


def notify(conn, events: list[differ.Event], *, dry_run: bool) -> None:
    ids = db.match_ids(conn)
    for event in events:
        match_id = ids.get(event.match)
        if match_id is None:
            continue
        if dry_run:
            telegram.send(event.text, dry_run=True)
            continue
        if not db.mark_sent(conn, match_id, event.kind):
            continue  # already went out on an earlier tick
        try:
            telegram.send(event.text)
        except Exception:  # noqa: BLE001
            # Release the reservation so the next run retries instead of
            # swallowing the message for good.
            db.unmark_sent(conn, match_id, event.kind)
            log.exception("delivery failed: %s", event.text)


def main(argv: list[str]) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    dry_run = "--dry-run" in argv
    offline = "--offline" in argv

    conn = db.connect()
    now = dt.datetime.now(dt.UTC)
    events = collect(conn, now, offline=offline)
    events += differ.due_reminders(db.stored_matches(conn), now)
    log.info("%d event(s)", len(events))
    notify(conn, events, dry_run=dry_run)


if __name__ == "__main__":
    main(sys.argv[1:])
