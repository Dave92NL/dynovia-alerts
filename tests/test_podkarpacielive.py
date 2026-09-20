"""Offline parser tests against tests/fixtures/podkarpacielive/ (2026-09-20)."""

import pytest

from dynovia.scrapers.podkarpacielive import (
    match_links,
    parse_goals,
    parse_results,
)
from dynovia.snapshot import load_fixtures

GROM = ("2026/27", "dynovia dynow", "grom handzlowka")


@pytest.fixture(scope="module")
def pages():
    return load_fixtures("podkarpacielive")


@pytest.fixture(scope="module")
def results(pages):
    return parse_results(pages["team"], "2026/27")


def test_the_repeated_form_list_is_collapsed(results):
    # The page renders the same six matches three times over.
    assert len(results) == 6
    assert len({m.external_id for m in results}) == 6


def test_the_score_is_flipped_back_when_dynovia_played_away(results):
    # The page shows "Markovia Markowa 4-1" meaning Dynovia won 4-1 away; the
    # real result is 1-4 to the hosts, and only the url slug says which it was.
    away_at_markowa = next(m for m in results if m.away.startswith("Dynovia"))
    assert (away_at_markowa.home, away_at_markowa.home_score) == ("Markovia Markowa", 1)
    assert away_at_markowa.away_score == 4


def test_home_matches_keep_their_order(results):
    versus_grom = next(m for m in results if m.external_id == "152553")
    assert (versus_grom.home, versus_grom.away) == ("Dynovia Dynów", "Grom Handzlówka")
    assert (versus_grom.home_score, versus_grom.away_score) == (3, 1)


def test_other_seasons_are_left_out(pages):
    # The form list reaches back into last season; those rows carry a date from
    # June and would otherwise be filed under 2026/27.
    assert parse_results(pages["team"], "2025/26")


def test_goals_carry_the_minute(pages):
    goals = parse_goals(pages["match_152553"], GROM, dynovia_at_home=True)
    assert [(g.player, g.minute) for g in goals] == [
        ("Filip Goleś", 40),
        ("Filip Goleś", 42),
        ("Arkadiusz Kłoda", 87),
    ]


def test_the_opponents_goals_are_not_ours(pages):
    # Grom scored in the 69th minute. Read from the wrong side, that is the
    # only goal the parser would return.
    theirs = parse_goals(pages["match_152553"], GROM, dynovia_at_home=False)
    assert [(g.player, g.minute) for g in theirs] == [("Maciej Kuźniar", 69)]


def test_match_links_expose_the_slug(pages):
    assert match_links(pages["team"])["152531"] == "markovia-markowa-vs-dynovia-dynow"
