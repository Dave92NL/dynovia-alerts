"""The hand-saved PZPN protocol: the only source of cards and playing time."""

import datetime as dt
import pathlib

import pytest

from dynovia import db, protokol
from dynovia.models import normalize_player
from dynovia.scrapers.ninetyminut import NinetyMinut
from dynovia.snapshot import load_fixtures

FIXTURE = pathlib.Path("tests/fixtures/laczynaspilka/grom-2026-09-19.html")
GROM = ("2026/27", "dynovia dynow", "grom handzlowka")
SQUAD = [
    "Tomasz Bielaszka", "Michael Londono Silva", "Krystian Skubisz",
    "Grzegorz Szczepański", "Sylwester Paszko", "Kamil Socha", "Jakob Dzik",
    "Filip Goleś", "Łukasz Kozioł", "Karol Stankiewicz", "Ruslan Kovtok",
    "Jakub Uryć", "Przemysław Barnaś", "Arkadiusz Kłoda", "Paweł Szczawiński",
    "Patryk Duchniak", "Sebastian Urbaniak", "Wojciech Mnich",
]
REGISTRY = {normalize_player(name): name for name in SQUAD}


@pytest.fixture(scope="module")
def protocol():
    return protokol.parse_protocol(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    result = NinetyMinut().parse(load_fixtures("90minut"))
    db.store_matches(connection, result.matches, result.source, dt.datetime.now(dt.UTC))
    yield connection
    connection.close()


def test_team_names_drop_the_abbreviation(protocol):
    # The full name and its three-letter code are separate divs; read together
    # they give "Dynovia DynówDYN", which matches no team anywhere.
    assert (protocol.home, protocol.away) == ("Dynovia Dynów", "Grom Handzlówka")


def test_both_squads_are_read(protocol):
    assert {team: len(squad) for team, squad in protocol.squads.items()} == {
        "Dynovia Dynów": 18,
        "Grom Handzlówka": 16,
    }


def test_events_are_told_apart_by_icon_colour(protocol):
    # The whole timeline, both teams: Grom scored once and were booked twice.
    kinds = [kind for _, _, kind in protocol.events]
    assert kinds.count("goal") == 4
    assert kinds.count("yellow") == 5
    assert kinds.count("on") == kinds.count("off") == 7


def test_the_squad_decides_whose_events_are_whose(protocol):
    # The timeline is shared: Grom's Kuźniar scored and two of their players
    # were booked in the same list as ours. The squad passed in is the only
    # thing that separates them.
    _, goals, cards = protokol.to_records(protocol, GROM, protokol.our_squad(protocol))
    assert [g.player for g in goals] == ["Arkadiusz Kłoda", "Filip Goleś", "Filip Goleś"]
    assert sorted(c.player for c in cards) == [
        "Krystian Skubisz",
        "Paweł Szczawiński",
        "Sylwester Paszko",
    ]

    _, theirs, their_cards = protokol.to_records(
        protocol, GROM, protokol.their_squad(protocol)
    )
    assert [g.player for g in theirs] == ["Maciej Kuźniar"]
    # Four of the five bookings belong to a squad. The fifth, Jan Kłusek in the
    # 55th, is in neither: the protocol books the bench staff too, and they are
    # listed under Sztab rather than in a squad. Filtering by squad drops him,
    # which is right - he did not play.
    assert [c.player for c in their_cards] == ["Dawid Kądzielawa"]


def test_playing_time_comes_from_the_substitutions(protocol):
    appearances, _, _ = protokol.to_records(protocol, GROM, protokol.our_squad(protocol))
    minutes = {a.player: (a.minute_out or 90) - (a.minute_in or 0) for a in appearances}
    assert minutes["Filip Goleś"] == 77  # started, off in the 77th
    assert minutes["Arkadiusz Kłoda"] == 30  # on in the 60th
    assert minutes["Tomasz Bielaszka"] == 90


def test_an_unused_substitute_did_not_play(protocol):
    appearances, _, _ = protokol.to_records(protocol, GROM, protokol.our_squad(protocol))
    assert len(appearances) == 15  # 11 + 4 who came on, of 18 named
    assert "Sebastian Urbaniak" not in {a.player for a in appearances}


def test_the_protocol_finds_its_match(conn, protocol):
    assert protokol.find_match(conn, protocol) == GROM


def test_the_reverse_fixture_is_not_picked(conn, protocol):
    # Dynovia play Grom twice, once each way. Matching on the unordered pair
    # would file an autumn protocol against the spring fixture.
    found = protokol.find_match(conn, protocol)
    assert found[1] == "dynovia dynow"


def ours_and_theirs(conn) -> tuple[int, int]:
    row = conn.execute(
        "SELECT SUM(p.ours) AS ours, SUM(1 - p.ours) AS theirs FROM appearances a"
        " JOIN players p ON p.id = a.player_id"
    ).fetchone()
    return row["ours"] or 0, row["theirs"] or 0


def test_importing_the_same_file_twice_changes_nothing(conn):
    protokol.import_file(conn, FIXTURE, REGISTRY)
    before = ours_and_theirs(conn)
    protokol.import_file(conn, FIXTURE, REGISTRY)
    assert ours_and_theirs(conn) == before == (15, 14)


def test_the_opposition_is_stored_but_never_counted_as_ours(conn):
    # Their names are not in kadra.txt and resolving them would raise a
    # question about every single one, so they are written down as-is - and
    # every statistic in the app has to keep ignoring them.
    protokol.import_file(conn, FIXTURE, REGISTRY)
    assert ours_and_theirs(conn) == (15, 14)

    from dynovia import stats

    played = dict(stats.appearances_by_player(conn, "2026/27"))
    assert "Maciej Kuźniar" not in played
    assert "Filip Goleś" in played
    assert dict(stats.goals_by_player(conn, "2026/27")).get("Maciej Kuźniar") is None


def test_full_names_in_the_protocol_need_no_questions(conn):
    # "Michael Steve Londono Silva" here, "Michael Londono Silva" in the roster.
    assert protokol.import_file(conn, FIXTURE, REGISTRY) == []


SCHEDULE = """
19.09.2026
Dynovia Dynów
3:1
Grom Handzlówka
Klasa A
Rozegrany
27.09.2026
Astra Medynia Głogowska
14:00
Dynovia Dynów
Klasa A
Nierozegrany
24.10.2026
Sawa Sonina
-:-
Dynovia Dynów
Klasa A
Nierozegrany
"""


def test_the_pzpn_schedule_reads_scores_kickoffs_and_blanks():
    played, upcoming, undated = protokol.parse_schedule(SCHEDULE)

    assert (played.home_score, played.away_score, played.status) == (3, 1, "finished")
    assert upcoming.time == dt.time(14, 0) and upcoming.status == "scheduled"
    assert undated.time is None and undated.home_score is None


def test_the_schedule_names_the_league():
    assert {m.competition for m in protokol.parse_schedule(SCHEDULE)} == {"Klasa A"}


def test_pzpn_outranks_the_scrapers_on_a_date(conn):
    from dynovia import db as store

    before = store.stored_matches(conn)
    key = next(k for k in before if k[2] == "stal ii lancut" or k[1] == "stal ii lancut")
    assert before[key].date == dt.date(2026, 10, 10)  # 90minut's version

    store.store_matches(
        conn,
        protokol.parse_schedule(
            "11.10.2026\nStal II Łańcut\n14:00\nDynovia Dynów\nKlasa A\nNierozegrany\n"
        ),
        protokol.SOURCE,
        dt.datetime.now(dt.UTC),
    )
    assert store.stored_matches(conn)[key].date == dt.date(2026, 10, 11)
