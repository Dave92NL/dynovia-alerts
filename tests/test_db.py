"""Storage rules that everything else depends on: one row per match across
runs, and notifications that cannot go out twice."""

import datetime as dt

import pytest

from dynovia import db
from dynovia.models import MatchData

NOW = dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.UTC)


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


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    yield connection
    connection.close()


def test_rescraping_the_same_match_updates_one_row(conn):
    db.store_matches(conn, [match()], "90minut", NOW)
    db.store_matches(
        conn, [match(home_score=2, away_score=1, status="finished")], "90minut", NOW
    )

    stored = db.stored_matches(conn)
    assert len(stored) == 1
    assert list(stored.values())[0].home_score == 2


def test_a_match_that_gains_a_date_stays_one_row(conn):
    # The whole reason the key is (season, home, away): late rounds are listed
    # without a date and get one later. Keying on the date would double them.
    db.store_matches(conn, [match(date=None, time=None)], "90minut", NOW)
    db.store_matches(conn, [match()], "90minut", NOW)

    stored = db.stored_matches(conn)
    assert len(stored) == 1
    assert list(stored.values())[0].date == dt.date(2026, 10, 3)


def test_a_later_dateless_scrape_does_not_wipe_a_known_date(conn):
    db.store_matches(conn, [match()], "90minut", NOW)
    db.store_matches(conn, [match(date=None, time=None)], "90minut", NOW)

    assert list(db.stored_matches(conn).values())[0].time == dt.time(16, 0)


def test_team_name_spelling_does_not_split_a_match(conn):
    db.store_matches(conn, [match()], "90minut", NOW)
    db.store_matches(conn, [match(home="LKS Dynovia Dynow")], "regiowyniki", NOW)

    assert len(db.stored_matches(conn)) == 1


def test_a_notification_is_only_ever_sent_once(conn):
    db.store_matches(conn, [match()], "90minut", NOW)
    match_id = list(db.match_ids(conn).values())[0]

    assert db.mark_sent(conn, match_id, "match_finished") is True
    assert db.mark_sent(conn, match_id, "match_finished") is False


def test_a_failed_delivery_can_be_retried(conn):
    db.store_matches(conn, [match()], "90minut", NOW)
    match_id = list(db.match_ids(conn).values())[0]

    db.mark_sent(conn, match_id, "reminder_1h")
    db.unmark_sent(conn, match_id, "reminder_1h")
    assert db.mark_sent(conn, match_id, "reminder_1h") is True


def test_last_fetch_reports_when_the_source_last_delivered(conn):
    assert db.last_fetch(conn, "90minut") is None
    db.store_matches(conn, [match()], "90minut", NOW)
    assert db.last_fetch(conn, "90minut") == NOW


def test_a_source_without_a_score_does_not_erase_one(conn):
    # futbolowo lists yesterday's match with an empty score cell while 90minut
    # already has 3-1. The later scrape must not wipe the result.
    db.store_matches(
        conn, [match(home_score=3, away_score=1, status="finished")], "90minut", NOW
    )
    db.store_matches(conn, [match()], "futbolowo", NOW)

    stored = list(db.stored_matches(conn).values())[0]
    assert (stored.home_score, stored.away_score, stored.status) == (3, 1, "finished")


def test_a_source_without_a_league_name_does_not_erase_one(conn):
    db.store_matches(conn, [match()], "90minut", NOW)
    db.store_matches(conn, [match(competition="")], "futbolowo", NOW)

    assert list(db.stored_matches(conn).values())[0].competition == "VI liga"


def test_articles_are_recorded_so_they_are_never_refetched(conn):
    from dynovia.models import MatchReport

    db.store_matches(conn, [match()], "90minut", NOW)
    report = MatchReport(
        url="https://example/news/article/x",
        title="Zwycięstwo",
        published_at=None,
        text="Dynovia: Bielaszka",
        match=match().key,
    )
    db.store_reports(conn, [report], "futbolowo", NOW)

    assert db.seen_articles(conn, "futbolowo") == {report.url}
    assert db.seen_articles(conn, "90minut") == set()
