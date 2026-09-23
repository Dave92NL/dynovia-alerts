"""The hand-saved PZPN protocol: the only source of cards and playing time."""

import datetime as dt
import pathlib

import pytest

from dynovia import db, protokol
from dynovia.models import normalize_player
from dynovia.scrapers.ninetyminut import NinetyMinut
from dynovia.snapshot import load_fixtures

FIXTURE = pathlib.Path("tests/fixtures/laczynaspilka/grom-2026-09-19.html")
OWN_GOAL = pathlib.Path("tests/fixtures/laczynaspilka/grodziszczanka-2026-09-13.html")
GROM = ("2026/27", "dynovia dynow", "grom handzlowka")
GRODZISZCZANKA = ("2026/27", "grodziszczanka grodzisko dolne", "dynovia dynow")
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
    kinds = [kind for _, _, kind, _ in protocol.events]
    assert kinds.count("goal") == 4
    assert kinds.count("yellow") == 5
    assert kinds.count("on") == kinds.count("off") == 7


def test_the_side_of_the_timeline_decides_whose_goal_it_is(protocol):
    # The timeline is shared: Grom's Kuźniar scored and two of their players
    # were booked in the same list as ours. Hosts sit on the left of it and
    # guests on the right, and that column is what separates them.
    _, goals, cards = protokol.to_records(protocol, GROM, protokol.our_team(protocol))
    assert [g.player for g in goals] == ["Arkadiusz Kłoda", "Filip Goleś", "Filip Goleś"]
    assert sorted(c.player for c in cards) == [
        "Krystian Skubisz",
        "Paweł Szczawiński",
        "Sylwester Paszko",
    ]

    _, theirs, their_cards = protokol.to_records(
        protocol, GROM, protokol.their_team(protocol)
    )
    assert [g.player for g in theirs] == ["Maciej Kuźniar"]
    # Four of the five bookings belong to a squad. The fifth, Jan Kłusek in the
    # 55th, is in neither: the protocol books the bench staff too, and they are
    # listed under Sztab rather than in a squad. Filtering by squad drops him,
    # which is right - he did not play.
    assert [c.player for c in their_cards] == ["Dawid Kądzielawa"]


def test_playing_time_comes_from_the_substitutions(protocol):
    appearances, _, _ = protokol.to_records(protocol, GROM, protokol.our_team(protocol))
    minutes = {a.player: (a.minute_out or 90) - (a.minute_in or 0) for a in appearances}
    assert minutes["Filip Goleś"] == 77  # started, off in the 77th
    assert minutes["Arkadiusz Kłoda"] == 30  # on in the 60th
    assert minutes["Tomasz Bielaszka"] == 90


def test_an_unused_substitute_did_not_play(protocol):
    appearances, _, _ = protokol.to_records(protocol, GROM, protokol.our_team(protocol))
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


# --- co z tego dociera do aplikacji ---------------------------------------


def test_both_squads_reach_the_app_with_their_side_marked(conn):
    from dynovia import export

    protokol.import_file(conn, FIXTURE, REGISTRY)
    match = [
        m for m in export.build(conn, "2026/27")["matches.json"]
        if m["date"] == "2026-09-19"
    ][0]

    ours = [p for p in match["lineup"] if p["ours"]]
    theirs = [p for p in match["lineup"] if not p["ours"]]
    assert (len(ours), len(theirs)) == (15, 14)
    # No team column anywhere: the side is `ours`, and the match says which
    # end of the fixture that is.
    assert {p["name"] for p in theirs} >= {"Maciej Kuźniar", "Dawid Kądzielawa"}

    # Their substitutions carry minutes too, or the screen cannot say when
    # anything happened.
    on = {p["name"]: p["minuteIn"] for p in theirs if p["minuteIn"]}
    assert on["Grzegorz Bekierski"] == 80

    assert [c["ours"] for c in match["cards"]] == [False, True, True, True]
    # The goal that makes it 3-1 belongs to them and comes from nowhere else.
    assert match["theirGoals"] == [{"player": "Maciej Kuźniar", "minute": 69}]


def test_our_goals_are_never_counted_from_two_sources_at_once(conn):
    from dynovia import export

    protokol.import_file(conn, FIXTURE, REGISTRY)
    match = [
        m for m in export.build(conn, "2026/27")["matches.json"]
        if m["date"] == "2026-09-19"
    ][0]
    # Three goals, not six: scorers_by_match picks one source for the match,
    # and theirGoals deliberately holds only the opposition.
    assert len(match["scorers"]) == 3
    assert all(g["player"] != "Maciej Kuźniar" for g in match["scorers"])


# --- samobój ---------------------------------------------------------------


@pytest.fixture(scope="module")
def own_goal_protocol():
    return protokol.parse_protocol(OWN_GOAL.read_text(encoding="utf-8"))


def test_an_own_goal_counts_for_the_team_that_did_not_score_it(own_goal_protocol):
    # Grodziszczanka 1-6 Dynovia. Seven goals on the timeline, and Kamil Papak
    # of Grodziszczanka put one of them past his own keeper: it sits in
    # Dynovia's column although his name is on it. Going by the name alone
    # gave us five goals and them two, which is not the result that was played.
    pr = own_goal_protocol
    _, ours, _ = protokol.to_records(pr, GRODZISZCZANKA, protokol.our_team(pr))
    _, theirs, _ = protokol.to_records(pr, GRODZISZCZANKA, protokol.their_team(pr))

    assert (len(ours), len(theirs)) == (6, 1)
    own = [g for g in ours if g.type == "own"]
    assert [(g.player, g.minute) for g in own] == [("Kamil Papak", 82)]
    assert all(g.type == "normal" for g in theirs)


def test_a_plain_goal_is_never_marked_as_an_own_one(protocol):
    _, ours, _ = protokol.to_records(protocol, GROM, protokol.our_team(protocol))
    _, theirs, _ = protokol.to_records(protocol, GROM, protokol.their_team(protocol))
    assert {g.type for g in ours + theirs} == {"normal"}


def test_an_own_goal_scorer_is_still_an_opposition_player(conn):
    # He is our sixth goal and someone else's player at the same time. Sending
    # him through kadra.txt would ask the owner who he is; leaving him out of
    # the roster check is what keeps that question from ever being asked.
    protokol.import_file(conn, OWN_GOAL, REGISTRY)
    row = conn.execute(
        "SELECT p.ours, g.type FROM goals g JOIN players p ON p.id = g.player_id"
        " WHERE p.name = 'Kamil Papak'"
    ).fetchone()
    assert (row["ours"], row["type"]) == (0, "own")

    from dynovia import stats

    # And he is nobody's scorer in our statistics.
    assert "Kamil Papak" not in dict(stats.goals_by_player(conn, "2026/27"))


def test_the_app_gets_an_own_goal_as_neither_side_scoring(conn):
    from dynovia import export

    protokol.import_file(conn, OWN_GOAL, REGISTRY)
    match = [
        m for m in export.build(conn, "2026/27")["matches.json"]
        if m["date"] == "2026-09-13"
    ][0]

    # Grodziszczanka scored once. Counting Papak among them made it two and
    # contradicted the 1-6 on the same screen.
    assert [g["player"] for g in match["theirGoals"]] == ["Kazimierz Leja"]
    assert match["ownGoals"] == [
        {"player": "Kamil Papak", "minute": 82, "ours": True}
    ]
    # And he is not one of our scorers either, because he is not our player.
    assert all(g["player"] != "Kamil Papak" for g in match["scorers"])


def test_every_match_gets_its_own_lists(conn):
    # _empty() is a function for a reason: a shared dict would pour every
    # match's squads into whichever match was seen first.
    from dynovia import export

    protokol.import_file(conn, OWN_GOAL, REGISTRY)
    protokol.import_file(conn, FIXTURE, REGISTRY)
    grane = [
        m for m in export.build(conn, "2026/27")["matches.json"]
        if m["date"] in ("2026-09-13", "2026-09-19")
    ]
    assert [len(m["lineup"]) for m in grane] == [29, 29]
    assert [len(m["ownGoals"]) for m in grane] == [1, 0]
