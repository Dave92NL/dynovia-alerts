"""Turns "what changed since the last run" into messages worth sending.

Conflicts between sources are raised in run.py, where merge.py reports them.
lineup_available and protocol_available follow once lineups are stored.

Kickoff times are Europe/Warsaw; GitHub Actions runs on UTC, so every comparison
goes through an aware datetime rather than a naive local one.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dynovia.models import MatchData, MatchKey

WARSAW = ZoneInfo("Europe/Warsaw")

REMINDERS = (
    (dt.timedelta(hours=24), "reminder_24h"),
    (dt.timedelta(hours=1), "reminder_1h"),
)


@dataclass(frozen=True, slots=True)
class Event:
    match: MatchKey
    kind: str
    """Together with the match id this is the deduplication key, so it has to
    carry the new value for anything that can change more than once."""
    text: str
    buttons: list | None = None
    """Inline keyboard, for the events that ask a question rather than report."""


def kickoff(match: MatchData) -> dt.datetime | None:
    """Aware kickoff instant, or None while the round has no date or time yet."""
    if match.date is None or match.time is None:
        return None
    return dt.datetime.combine(match.date, match.time, tzinfo=WARSAW)


def diff(
    old: dict[MatchKey, MatchData], new: list[MatchData], now: dt.datetime
) -> list[Event]:
    if not old:
        # First run pulls a whole season at once. Storing it silently beats
        # firing 28 notifications about matches that are mostly already played.
        return []

    events: list[Event] = []
    for match in new:
        before = old.get(match.key)
        if before is None:
            if _is_upcoming(match, now):
                events.append(
                    Event(match.key, "match_scheduled", f"📅 Nowy mecz: {_line(match)}")
                )
            continue
        if match.date and (before.date, before.time) != (match.date, match.time):
            # Three different things wear the same shape. A dateless round
            # getting a date is an announcement; a dated round getting its
            # kickoff is a second announcement; only an actual move is a move.
            if before.date is None:
                kind, label = "time_set", "📅 Termin wyznaczony"
            elif before.date == match.date and before.time is None:
                kind, label = "time_set", "🕐 Godzina wyznaczona"
            else:
                kind, label = "time_changed", "🕐 Zmiana terminu"
            events.append(
                Event(
                    match.key,
                    f"{kind}:{match.date} {match.time}",
                    f"{label}: {_line(match)}",
                )
            )
        if before.status != "finished" and match.status == "finished":
            events.append(
                Event(
                    match.key,
                    "match_finished",
                    f"🏁 KONIEC: {match.home} {match.home_score}"
                    f"–{match.away_score} {match.away}",
                )
            )
    return events


def due_reminders(
    matches: dict[MatchKey, MatchData], now: dt.datetime
) -> list[Event]:
    """Reminders are thresholds, not windows: fire as soon as now is past
    kickoff minus the lead time and let notifications_sent keep it to one
    message. A window would be missed whenever the cron runs late, which it does.
    """
    events: list[Event] = []
    for key, match in matches.items():
        start = kickoff(match)
        if start is None or match.status == "finished" or now >= start:
            continue
        for lead, kind in REMINDERS:
            if now >= start - lead:
                events.append(Event(key, kind, _reminder_text(kind, match, now)))
    return events


def _reminder_text(kind: str, match: MatchData, now: dt.datetime) -> str:
    if kind == "reminder_1h":
        return f"🔔 Za godzinę: {match.home} – {match.away}"
    return f"⚽ {_when(match, now)}: {match.home} – {match.away}{_round(match)}"


def _is_upcoming(match: MatchData, now: dt.datetime) -> bool:
    """A match with no date yet is still news; one already played is not."""
    return match.date is None or match.date >= now.astimezone(WARSAW).date()


def _line(match: MatchData) -> str:
    when = _when(match, None) if match.date else "termin nieznany"
    return f"{match.home} – {match.away}{_round(match)} → {when}"


def _when(match: MatchData, now: dt.datetime | None) -> str:
    """'jutro 16:00' only when it really is tomorrow - a late cron tick or a
    match added at the last moment must not announce yesterday as tomorrow."""
    start = kickoff(match)
    if start is None:
        return f"{match.date:%d.%m}" if match.date else "termin nieznany"
    local = start.astimezone(WARSAW)
    if now is not None:
        days = (local.date() - now.astimezone(WARSAW).date()).days
        if days == 0:
            return f"Dziś {local:%H:%M}"
        if days == 1:
            return f"Jutro {local:%H:%M}"
    return f"{local:%d.%m} o {local:%H:%M}"


def _round(match: MatchData) -> str:
    return f" (kolejka {match.round})" if match.round else ""
