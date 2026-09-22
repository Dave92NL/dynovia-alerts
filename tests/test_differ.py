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

    def after(**offset) -> dt.timedelta:
        return run.poll_interval(played, KICKOFF + dt.timedelta(**offset))

    assert after(hours=1) == dt.timedelta(minutes=10)
    # The old window closed here, which is the point of the change: a 16:00
    # kickoff whistles at 17:50 and the result is published after that.
    assert after(hours=4) == dt.timedelta(minutes=10)
    assert after(hours=6) == dt.timedelta(minutes=10)
    assert after(hours=7) == dt.timedelta(minutes=30)
    assert after(hours=24) == dt.timedelta(minutes=30)
    assert after(hours=25) == dt.timedelta(hours=2)
    assert after(days=5) == run.IDLE


def test_two_matches_in_a_weekend_do_not_depend_on_list_order():
    # The nearer match wins whichever way round the list comes.
    sunday = match(date=dt.date(2026, 10, 4), away="Sawa Sonina")
    now = KICKOFF + dt.timedelta(hours=26)  # slow for Saturday, fast for Sunday
    assert run.poll_interval([match(), sunday], now) == dt.timedelta(minutes=10)
    assert run.poll_interval([sunday, match()], now) == dt.timedelta(minutes=10)


def test_the_daily_refresh_waits_for_six_in_the_morning():
    quiet_day = [match(date=dt.date(2027, 1, 1))]
    yesterday = dt.datetime(2026, 11, 20, 8, 0, tzinfo=differ.WARSAW)

    before_six = dt.datetime(2026, 11, 21, 5, 0, tzinfo=differ.WARSAW)
    after_six = dt.datetime(2026, 11, 21, 6, 30, tzinfo=differ.WARSAW)
    assert run.is_due(yesterday, quiet_day, before_six) is False
    assert run.is_due(yesterday, quiet_day, after_six) is True


def test_a_source_never_fetched_is_always_due():
    assert run.is_due(None, [match()], KICKOFF) is True


# --- gole i protokół po gwizdku -------------------------------------------


def goal(player: str, minute: int | None = None) -> dict:
    return {"player": player, "minute": minute}


def played(name: str, started=True, minute_in=None, minute_out=None) -> dict:
    return {
        "name": name,
        "started": started,
        "minute_in": minute_in,
        "minute_out": minute_out,
    }


def card(name: str, color: str = "yellow", minute: int | None = None) -> dict:
    return {"name": name, "color": color, "minute": minute}


def details(scorers=(), squad=(), cards=(), now=None, **overrides):
    m = match(**overrides)
    return differ.match_details(
        m.key, m, list(scorers), list(squad), list(cards), now or KICKOFF
    )


def test_only_matches_that_kicked_off_recently_are_looked_at():
    now = KICKOFF + dt.timedelta(hours=1)
    assert differ.fresh_matches(stored(match()), now) == [match().key]
    assert differ.fresh_matches(stored(match()), KICKOFF - dt.timedelta(minutes=1)) == []
    # Three days on, a goal is archaeology - and a rebuilt database must not
    # replay the whole season into the phone.
    assert differ.fresh_matches(stored(match()), KICKOFF + differ.AFTERMATH * 2) == []


def test_a_match_with_no_kickoff_yet_is_never_fresh():
    assert differ.fresh_matches(stored(match(date=None, time=None)), KICKOFF) == []


def test_every_goal_becomes_its_own_message():
    events = details(scorers=[goal("Filip Goleś", 40), goal("Ruslan Kovtok", 67)])
    assert kinds(events) == ["goal", "goal"]
    assert "Filip Goleś 40'" in events[0].text


def test_a_corrected_minute_is_not_a_second_goal():
    # One source says 40', a more trusted one later says 42'. Same goal.
    first = details(scorers=[goal("Filip Goleś", 40)])
    again = details(scorers=[goal("Filip Goleś", 42)])
    assert first[0].kind == again[0].kind


def test_a_brace_is_two_messages_even_without_minutes():
    events = details(scorers=[goal("Filip Goleś"), goal("Filip Goleś")])
    assert len({e.kind for e in events}) == 2


def test_the_protocol_reports_the_squad_and_both_directions_of_a_change():
    events = details(
        squad=[
            played("Arkadiusz Kłoda", minute_out=90),
            played("Kamil Socha", started=False, minute_in=60),
            played("Sylwester Paszko", minute_out=60),
        ]
    )
    assert kinds(events) == ["protocol_ready"]
    text = events[0].text
    assert "Skład: Arkadiusz Kłoda, Sylwester Paszko" in text
    assert "⬆️ Kamil Socha 60'" in text and "⬇️ Sylwester Paszko 60'" in text


def test_playing_to_the_whistle_is_not_a_substitution():
    # The protocol stamps minute_out = 90 on everyone still on the pitch, so a
    # naive read turns a full squad into eleven substitutions.
    events = details(squad=[played(f"Gracz {i}", minute_out=90) for i in range(11)])
    assert "Zmiany" not in events[0].text


def test_cards_ride_along_in_the_protocol_message():
    # They only ever arrive with the protocol, so a separate buzz would just
    # mean two notifications for one import.
    events = details(
        squad=[played("Krystian Skubisz")],
        cards=[card("Krystian Skubisz", minute=60), card("Jakob Dzik", "red", 80)],
    )
    assert kinds(events) == ["protocol_ready"]
    assert "Kartki: 🟨 Krystian Skubisz 60' · 🟥 Jakob Dzik 80'" in events[0].text


def test_a_card_is_never_the_thing_that_goes_missing():
    # No squad stored yet for whatever reason: the cards still have to speak.
    events = details(cards=[card("Krystian Skubisz", minute=60)])
    assert kinds(events) == ["protocol_ready"]
    assert "Skład" not in events[0].text


def test_a_second_yellow_is_not_a_straight_red():
    events = details(cards=[card("Jakob Dzik", "second_yellow", 80)])
    assert "🟨🟥 Jakob Dzik 80'" in events[0].text


def test_no_protocol_means_no_protocol_message():
    assert details(scorers=[goal("Filip Goleś", 40)]) != []
    assert kinds(details(scorers=[goal("Filip Goleś", 40)])) == ["goal"]


A_DAY_LATER = KICKOFF + differ.PROTOCOL_NAG


def test_a_played_match_with_no_protocol_asks_for_one():
    events = details(status="finished", now=A_DAY_LATER)
    assert kinds(events) == ["protocol_missing"]
    assert "Ctrl+S" in events[0].text


def test_the_nag_waits_a_day_before_asking():
    # The delegate fills the protocol in over the following hours; asking at
    # the final whistle would be noise.
    assert details(status="finished", now=KICKOFF + dt.timedelta(hours=3)) == []


def test_a_match_still_being_played_is_never_nagged():
    assert details(now=A_DAY_LATER) == []


def test_an_imported_protocol_silences_the_nag():
    events = details(
        squad=[played("Krystian Skubisz")], status="finished", now=A_DAY_LATER
    )
    assert kinds(events) == ["protocol_ready"]
