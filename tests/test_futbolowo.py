"""Offline parser tests against tests/fixtures/futbolowo/ (snapshot 2026-09-20)."""

import datetime as dt

import pytest

from dynovia.models import normalize_team
from dynovia.scrapers.futbolowo import (
    Futbolowo,
    parse_article_list,
    parse_fixtures,
    parse_report,
)
from dynovia.snapshot import load_fixtures

GRODZISKO = "article_zwyciestwo-w-grodzisku-na-szostke"
PLANTATOR = "article_zwyciestwo-z-kolejnym-beniaminkiem"


@pytest.fixture(scope="module")
def pages():
    return load_fixtures("futbolowo")


@pytest.fixture(scope="module")
def result(pages):
    return Futbolowo().parse(pages)


def test_only_dynovia_rows_are_kept(pages):
    # The page lists the whole league plus a "Pauza" row per round.
    matches = parse_fixtures(pages["terminarz"])
    assert len(matches) == 6
    assert all("dynovia" in f"{m.home} {m.away}".lower() for m in matches)


def test_the_site_tagline_is_stripped_from_the_club_name(pages):
    # The cell literally reads "Dynovia Dynów – oficjalna strona klubowa"; left
    # alone it would never join with 90minut's "Dynovia Dynów".
    matches = parse_fixtures(pages["terminarz"])
    assert normalize_team(matches[0].home) == "dynovia dynow"


def test_opponent_names_normalise_to_the_same_key_as_90minut(pages):
    # futbolowo writes "Ks Polonia Hyżne" where 90minut writes "Polonia Hyżne".
    keys = {normalize_team(m.away) for m in parse_fixtures(pages["terminarz"])}
    assert "polonia hyzne" in keys


def test_scores_and_kickoffs_are_read(pages):
    away_at_grodzisko = parse_fixtures(pages["terminarz"])[4]
    assert away_at_grodzisko.date == dt.date(2026, 9, 13)
    assert away_at_grodzisko.time == dt.time(16, 30)
    assert (away_at_grodzisko.home_score, away_at_grodzisko.away_score) == (1, 6)
    assert away_at_grodzisko.season == "2026/27"


def test_the_league_name_is_left_blank_not_invented(pages):
    # futbolowo names the competition nowhere, so merge keeps 90minut's name.
    assert {m.competition for m in parse_fixtures(pages["terminarz"])} == {""}


def test_article_list_has_absolute_urls_and_timestamps(pages):
    articles = parse_article_list(pages["news"])
    assert len(articles) == 18
    url, title, published = articles[0]
    assert url.startswith("https://dynovia-dynow.futbolowo.pl/news/article/")
    assert title
    assert published == dt.datetime(
        2026, 9, 16, 17, 50, tzinfo=dt.timezone(dt.timedelta(hours=2))
    )


def test_every_report_is_tied_to_the_right_match(result):
    # Publication dates are days off the real kickoff, so this has to work off
    # the teams named in the report.
    tied = {r.title: r.match for r in result.reports}
    assert tied["Zwycięstwo w Grodzisku na szóstkę!"] == (
        "2026/27",
        "grodziszczanka grodzisko dolne",
        "dynovia dynow",
    )
    assert tied["Zwycięstwo z Gromem!"] == (
        "2026/27",
        "dynovia dynow",
        "grom handzlowka",
    )
    assert all(r.match is not None for r in result.reports)


def test_the_goal_line_is_not_mistaken_for_a_lineup(result):
    # That report reads "Bramki Dynovia: Paszko, Kovtok, ..." and has no lineup
    # at all; an unanchored pattern would file five scorers as starters.
    grodzisko = ("2026/27", "grodziszczanka grodzisko dolne", "dynovia dynow")
    assert [a for a in result.lineups if a.match == grodzisko] == []


def test_a_lineup_splits_into_starters_and_substitutes(result):
    plantator = ("2026/27", "dynovia dynow", "plantator nienadowka")
    lineup = [a for a in result.lineups if a.match == plantator]
    assert len([a for a in lineup if a.started]) == 11
    assert [a.player for a in lineup if not a.started] == ["Barnaś", "Socha", "Kustra"]


def test_the_lineup_stops_before_the_prose(result):
    # The report runs "...Kłoda. Na zmiany: Barnaś, Socha, Kustra." straight on
    # into narrative with no markup between, so the run has to end on its own.
    assert len(result.lineups) == 14


def test_initials_survive_the_split(result):
    plantator = ("2026/27", "dynovia dynow", "plantator nienadowka")
    assert [g.player for g in result.goals if g.match == plantator] == [
        "S. Paszko",
        "P. Szczawiński",
    ]


def test_own_goals_are_not_credited_to_a_player(result):
    # "Bramki Dynovia: Paszko, Kovtok, Socha, Kłoda, gol sam. Duchniak" is six
    # goals, five of them ours - and Duchniak follows a full stop, not a comma.
    grodzisko = ("2026/27", "grodziszczanka grodzisko dolne", "dynovia dynow")
    scorers = [g.player for g in result.goals if g.match == grodzisko]
    assert scorers == ["Paszko", "Kovtok", "Socha", "Kłoda", "Duchniak"]


def test_an_article_that_is_not_a_report_yields_nothing_but_is_still_kept():
    # It still has to be recorded, or it gets downloaded again every single run.
    html = (
        "<section class='post-content'><p>Jakub Bury zawodnikiem Dynovii. "
        "Witamy w klubie!</p></section>"
    )
    report, lineups, goals = parse_report(
        html, "https://x/news/article/jakub-bury", "Jakub Bury", None, []
    )
    assert (lineups, goals) == ([], [])
    assert report.match is None
    assert report.text
