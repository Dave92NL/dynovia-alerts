"""How disagreeing sources are resolved, and what counts as a disagreement."""

import datetime as dt

from dynovia import merge
from dynovia.models import MatchData


def view(**overrides) -> MatchData:
    defaults = dict(
        season="2026/27",
        date=dt.date(2026, 10, 10),
        time=None,
        competition="VI liga",
        home="Stal II Łańcut",
        away="Dynovia Dynów",
        round=9,
    )
    return MatchData(**(defaults | overrides))


def test_the_trusted_source_wins_regardless_of_write_order():
    # Round 9: 90minut says 10 October, regiowyniki says the 11th, and the PZPN
    # schedule says the 11th - which is why regiowyniki leads on dates.
    for order in ("regiowyniki", "90minut"), ("90minut", "regiowyniki"):
        views = {
            order[0]: view(date=dt.date(2026, 10, 11 if order[0] == "regiowyniki" else 10)),
            order[1]: view(date=dt.date(2026, 10, 10 if order[1] == "90minut" else 11)),
        }
        merged, _ = merge.merge(views)
        assert merged.date == dt.date(2026, 10, 11)


def test_the_loser_is_still_reported():
    views = {
        "90minut": view(date=dt.date(2026, 10, 10)),
        "regiowyniki": view(date=dt.date(2026, 10, 11)),
    }
    _, conflicts = merge.merge(views)
    assert [(c.field, c.source_a, c.source_b) for c in conflicts] == [
        ("date", "regiowyniki", "90minut")
    ]


def test_a_missing_value_is_a_gap_not_a_disagreement():
    # futbolowo lists yesterday's match with no score yet. That is nothing to
    # argue about, and it must not raise an alert.
    views = {
        "90minut": view(home_score=3, away_score=1, status="finished"),
        "futbolowo": view(),
    }
    merged, conflicts = merge.merge(views)
    assert (merged.home_score, merged.status) == (3, "finished")
    assert conflicts == []


def test_a_lesser_source_fills_a_gap_the_leader_left():
    views = {"90minut": view(time=None), "regiowyniki": view(time=dt.time(14, 0))}
    merged, conflicts = merge.merge(views)
    assert merged.time == dt.time(14, 0)
    assert conflicts == []


def test_the_league_name_is_settled_quietly():
    # 90minut says "VI liga" and regiowyniki "Klasa A" for every match of the
    # season. Alerting on that would bury the conflicts that matter.
    views = {"90minut": view(), "regiowyniki": view(competition="Klasa A")}
    merged, conflicts = merge.merge(views)
    assert merged.competition == "Klasa A"
    assert conflicts == []


def test_one_source_is_enough():
    merged, conflicts = merge.merge({"90minut": view()})
    assert merged.date == dt.date(2026, 10, 10)
    assert conflicts == []
