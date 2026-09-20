"""Which changes turn into a message, and when reminders fire."""

import datetime as dt

from dynovia import differ, run
from dynovia.models import MatchData

# 16:00 Warsaw on 3 Oct 2026 - the kickoff every case below is measured against.
KICKOFF = dt.datetime(2026, 10, 3, 16, 0, tzinfo=differ.WARSAW)


def match(**overrides) -> MatchData:
    defaults = dict(
        season="2026/27",
        date=dt.date(2026, 10, 3),
        time=dt.time(16, 0),
        competition="VI liga",
        home="Dynovia Dynów",
        away="Crasnovia Krasne",
        round=8,
    )
    return MatchData(**(defaults | overrides))


def stored(*matches) -> dict:
    return {m.key: m for m in matches}


def kinds(events) -> list[str]:
    return [e.kind.split(":")[0] for e in events]


def test_the_first_run_stays_silent():
    # An empty database means a whole season arrives at once, most of it played.
    assert differ.diff({}, [match()], KICKOFF) == []


def test_a_new_future_match_is_announced():
    old = stored(match(away="Sawa Sonina"))
    events = differ.diff(old, [match()], KICKOFF - dt.timedelta(days=5))
    assert kinds(events) == ["match_scheduled"]


def test_a_new_match_already_played_is_not_announced():
    old = stored(match(away="Sawa Sonina"))
    assert differ.diff(old, [match()], KICKOFF + dt.timedelta(days=5)) == []


def test_a_date_appearing_for_the_first_time_is_not_a_reschedule():
    old = stored(match(date=None, time=None))
    events = differ.diff(old, [match()], KICKOFF - dt.timedelta(days=5))
    assert kinds(events) == ["time_set"]
    assert "Termin wyznaczony" in events[0].text


def test_a_moved_kickoff_is_a_reschedule():
    old = stored(match())
    events = differ.diff(old, [match(time=dt.time(11, 0))], KICKOFF - dt.timedelta(days=1))
    assert kinds(events) == ["time_changed"]


def test_two_reschedules_are_two_distinct_notifications():
    # The dedup key is (match id, kind), so the kind has to carry the new value
    # or the second change would be silently swallowed.
    first = differ.diff(stored(match()), [match(time=dt.time(11, 0))], KICKOFF)
    second = differ.diff(
        stored(match(time=dt.time(11, 0))), [match(time=dt.time(13, 0))], KICKOFF
    )
    assert first[0].kind != second[0].kind


def test_a_result_appearing_is_announced_once():
    old = stored(match())
    finished = match(home_score=2, away_score=1, status="finished")
    events = differ.diff(old, [finished], KICKOFF + dt.timedelta(hours=2))
    assert kinds(events) == ["match_finished"]
    assert events[0].text == "🏁 KONIEC: Dynovia Dynów 2–1 Crasnovia Krasne"

    assert differ.diff(stored(finished), [finished], KICKOFF) == []


def test_reminders_fire_from_their_threshold_onwards():
    upcoming = stored(match())
    assert kinds(differ.due_reminders(upcoming, KICKOFF - dt.timedelta(hours=30))) == []
    assert kinds(differ.due_reminders(upcoming, KICKOFF - dt.timedelta(hours=20))) == [
        "reminder_24h"
    ]
    # Threshold, not window: a cron tick running late still gets both, and
    # notifications_sent is what keeps each of them to a single message.
    assert kinds(differ.due_reminders(upcoming, KICKOFF - dt.timedelta(minutes=5))) == [
        "reminder_24h",
        "reminder_1h",
    ]


def test_no_reminders_after_kickoff_or_without_a_time():
    assert differ.due_reminders(stored(match()), KICKOFF) == []
    assert differ.due_reminders(stored(match(time=None)), KICKOFF) == []


def test_the_day_before_is_called_tomorrow_only_when_it_is():
    day_before = differ.due_reminders(stored(match()), KICKOFF - dt.timedelta(hours=23))
    assert "Jutro 16:00" in day_before[0].text

    same_day = differ.due_reminders(stored(match()), KICKOFF - dt.timedelta(hours=4))
    assert "Dziś 16:00" in same_day[0].text


def test_polling_speeds_up_during_and_after_a_match():
    played = [match()]
    assert run.poll_interval(played, KICKOFF + dt.timedelta(hours=1)) == dt.timedelta(
        minutes=10
    )
    assert run.poll_interval(played, KICKOFF + dt.timedelta(hours=24)) == dt.timedelta(
        hours=2
    )
    assert run.poll_interval(played, KICKOFF + dt.timedelta(days=5)) == dt.timedelta(
        days=1
    )


def test_the_daily_refresh_waits_for_six_in_the_morning():
    quiet_day = [match(date=dt.date(2027, 1, 1))]
    yesterday = dt.datetime(2026, 11, 20, 8, 0, tzinfo=differ.WARSAW)

    before_six = dt.datetime(2026, 11, 21, 5, 0, tzinfo=differ.WARSAW)
    after_six = dt.datetime(2026, 11, 21, 6, 30, tzinfo=differ.WARSAW)
    assert run.is_due(yesterday, quiet_day, before_six) is False
    assert run.is_due(yesterday, quiet_day, after_six) is True


def test_a_source_never_fetched_is_always_due():
    assert run.is_due(None, [match()], KICKOFF) is True
