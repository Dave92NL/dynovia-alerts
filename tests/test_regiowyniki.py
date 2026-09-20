"""Offline parser tests against tests/fixtures/regiowyniki/ (snapshot 2026-09-20)."""

import datetime as dt
from collections import Counter

import pytest

from dynovia.scrapers.regiowyniki import parse_matches
from dynovia.snapshot import load_fixtures


@pytest.fixture(scope="module")
def matches():
    return parse_matches(load_fixtures("regiowyniki")["team"], "2026/27")


def test_the_whole_season_is_parsed(matches):
    assert len(matches) == 28


def test_the_league_is_named_the_way_a_human_would(matches):
    # The only source that does. 90minut prints "VI liga", futbolowo nothing.
    assert {m.competition for m in matches} == {"Klasa A"}


def test_the_year_comes_from_the_season_because_the_page_omits_it(matches):
    # Dates read "16sie/0817:00" - day, month, kickoff, and no year anywhere.
    assert matches[0].date == dt.date(2026, 8, 16)
    assert matches[0].time == dt.time(17, 0)


def test_spring_rounds_land_in_the_following_calendar_year():
    spring = parse_matches(load_fixtures("regiowyniki")["team"], "2025/26")
    assert spring[0].date == dt.date(2025, 8, 16)


def test_unscheduled_rounds_are_kept_without_a_date(matches):
    undated = [m for m in matches if m.date is None]
    assert undated
    assert all(m.status == "scheduled" for m in undated)


def test_played_matches_carry_the_sources_own_id(matches):
    played = [m for m in matches if m.status == "finished"]
    assert len(played) == 6
    assert all(m.external_id and m.external_id.isdigit() for m in played)


def test_the_source_lists_one_fixture_twice(matches):
    # Documented, not fixed: regiowyniki puts Dynovia at home in round 16 where
    # 90minut puts Dąbrówki, so the two rows collapse onto one key. store_matches
    # keeps the first and raises it as a conflict instead of losing it quietly.
    duplicates = [key for key, n in Counter(m.key for m in matches).items() if n > 1]
    assert duplicates == [("2026/27", "dynovia dynow", "dabrowki")]
