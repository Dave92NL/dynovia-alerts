"""Bot commands and the buttons that settle a question for good."""

import datetime as dt

import pytest

from dynovia import bot, db, protokol
from dynovia.merge import Conflict
from dynovia.models import normalize_player
from dynovia.scrapers import SCRAPERS
from dynovia.snapshot import load_fixtures

NOW = dt.datetime.now(dt.UTC)
REGISTRY = {
    normalize_player(name): name
    for name in ("Filip Goleś", "Arkadiusz Kłoda", "Andrzej Goleś")
}


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    for name in ("90minut", "podkarpacielive"):
        result = SCRAPERS[name]().parse(load_fixtures(name))
        db.store_matches(connection, result.matches, result.source, NOW)
        db.store_goals(connection, result.goals, result.source, REGISTRY)
    yield connection
    connection.close()


def test_an_unknown_command_answers_with_the_list(conn):
    text, _ = bot.handle_command(conn, "/cokolwiek")
    assert "/nastepny" in text and "/konflikty" in text


def test_next_match_skips_the_ones_already_played(conn):
    text, _ = bot.handle_command(conn, "/nastepny")
    assert "Astra Medynia Głogowska – Dynovia Dynów" in text
    assert "27.09" in text


def test_last_match_lists_scorers_from_one_source_only(conn):
    # futbolowo and podkarpacielive both saw the Grom match; counting the union
    # would put five scorers under a 3-1.
    text, _ = bot.handle_command(conn, "/ostatni")
    assert "3–1 Grom Handzlówka" in text
    assert text.count("⚽") == 3


def test_a_conflict_comes_with_one_button_per_version(conn):
    db.store_matches(
        conn,
        protokol.parse_schedule(
            "11.10.2026\nStal II Łańcut\n14:00\nDynovia Dynów\nKlasa A\nNierozegrany\n"
        ),
        "laczynaspilka",
        NOW,
    )
    text, buttons = bot.handle_command(conn, "/konflikty")
    assert "Stal II Łańcut" in text
    assert [label for row in buttons for label, _ in row] == [
        "laczynaspilka: 2026-10-11",
        "90minut: 2026-10-10",
    ]


def test_an_answered_conflict_beats_the_trust_order(conn):
    db.store_matches(
        conn,
        protokol.parse_schedule(
            "11.10.2026\nStal II Łańcut\n14:00\nDynovia Dynów\nKlasa A\nNierozegrany\n"
        ),
        "laczynaspilka",
        NOW,
    )
    conflict = db.open_conflicts(conn)[0]
    key = ("2026/27", "stal ii lancut", "dynovia dynow")

    # Deliberately the weaker source: the point is that the answer wins.
    bot.handle_callback(conn, f"c:{conflict['id']}:b")
    assert db.stored_matches(conn)[key].date == dt.date(2026, 10, 10)
    assert db.open_conflicts(conn) == []


def test_answering_who_a_player_is_records_the_alias(conn):
    match_id = next(iter(db.match_ids(conn).values()))
    db.record_conflict(
        conn,
        match_id,
        Conflict("zawodnik", "futbolowo", "Goleś", "kadra", "Andrzej Goleś, Filip Goleś"),
        NOW.isoformat(),
    )
    conflict = db.open_conflicts(conn)[0]

    answer = bot.handle_callback(conn, f"p:{conflict['id']}:1")
    assert "Filip Goleś" in answer
    assert db.open_conflicts(conn) == []

    # And the answer sticks: the same spelling now resolves without asking.
    row = conn.execute(
        "SELECT p.name FROM player_aliases a JOIN players p ON p.id = a.player_id"
        " WHERE a.alias = 'goles' AND a.source = 'futbolowo'"
    ).fetchone()
    assert row["name"] == "Filip Goleś"


def test_polling_advances_the_offset_even_when_a_command_blows_up(conn, monkeypatch):
    sent = []
    monkeypatch.setattr(
        bot.telegram, "get_updates", lambda offset: [{"update_id": 41, "message": {"text": "/tabela"}}]
    )
    monkeypatch.setattr(bot.telegram, "send", lambda *a, **k: sent.append(a))

    bot.poll(conn)
    assert db.get_setting(conn, bot.OFFSET_KEY) == "42"
    assert sent
