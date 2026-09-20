"""Offline parser tests against tests/fixtures/90minut/ (snapshot 2026-09-20)."""

import datetime as dt

import pytest

from dynovia.scrapers.base import ScraperError
from dynovia.scrapers.ninetyminut import latest_season, parse_matches
from dynovia.snapshot import load_fixtures


@pytest.fixture(scope="module")
def pages():
    return load_fixtures("90minut")


def test_season_is_detected_as_the_newest_in_the_dropdown(pages):
    season_id, label = latest_season(pages["seasons"])
    assert (season_id, label) == (109, "2026/27")


def test_missing_dropdown_is_an_error(pages):
    with pytest.raises(ScraperError):
        latest_season("<html><body>nothing here</body></html>")


def test_full_season_schedule_is_parsed(pages):
    # 28, not 30: the league has an odd number of teams and Dynovia sits out
    # rounds 15 and 30, so those rounds are absent from the club's fixture list.
    assert len(parse_matches(pages["matches"])) == 28


def test_played_match_carries_date_time_round_and_score(pages):
    away_at_markowa = parse_matches(pages["matches"])[2]
    assert away_at_markowa.date == dt.date(2026, 8, 30)
    assert away_at_markowa.time == dt.time(11, 0)
    assert away_at_markowa.competition == "VI liga"
    assert away_at_markowa.round == 3
    assert (away_at_markowa.home, away_at_markowa.away) == (
        "Markovia Markowa",
        "Dynovia Dynów",
    )
    assert (away_at_markowa.home_score, away_at_markowa.away_score) == (1, 4)
    assert away_at_markowa.status == "finished"


def test_iso_8859_2_is_decoded_not_mangled(pages):
    names = {m.home for m in parse_matches(pages["matches"])}
    assert "KS Dąbrówki" in names
    assert "Stal II Łańcut" in names


def test_rounds_without_a_date_are_kept_as_scheduled(pages):
    undated = [m for m in parse_matches(pages["matches"]) if m.date is None]
    assert undated, "late rounds are listed with no date yet - do not drop them"
    assert all(m.status == "scheduled" and m.home_score is None for m in undated)


def test_a_page_without_a_match_table_parses_to_nothing(pages):
    # The season-picker page has five-column tables too; the club-name guard in
    # the parser is what keeps them out. fetch() then turns this into an error.
    assert parse_matches(pages["seasons"]) == []
