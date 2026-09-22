"""Bot commands and the buttons that settle a question for good."""

import datetime as dt
import pathlib
import plistlib

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


OWNER = 999


@pytest.fixture(autouse=True)
def owner_chat(monkeypatch):
    """Updates in these tests come from the owner unless a case says otherwise."""
    monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", str(OWNER))


def message(**fields) -> dict:
    return {"chat": {"id": OWNER}, **fields}


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
        bot.telegram, "get_updates", lambda offset: [{"update_id": 41, "message": message(text="/tabela")}]
    )
    monkeypatch.setattr(bot.telegram, "send", lambda *a, **k: sent.append(a))
    db.set_setting(conn, bot.OFFSET_KEY, "1")  # not a fresh bot

    bot.poll(conn)
    assert db.get_setting(conn, bot.OFFSET_KEY) == "42"
    assert sent


def test_the_prose_quoted_is_the_one_describing_the_goal():
    report = (
        "Dynovia Dynów 4:1 Markovia Markowa\n"
        "Bramki: Kłoda, Londono\n"
        "Dynovia : Bielaszka, Kozioł, Kłoda, Londono\n"
        "Wynik meczu otworzył Arkadiusz Kłoda dostawiając nogę po dokładnym "
        "dograniu piłki przez Ruslana Kovtoka. "
        "Drugi gol to indywidualna akcja Michaela Londono zakończona strzałem."
    )
    # Not the lineup and not the goal line, both of which name him too.
    assert bot._paragraph_about(report, "Arkadiusz Kłoda").startswith("Wynik meczu")
    # Polish inflects the name and the surname is not the last word.
    assert "indywidualna" in bot._paragraph_about(report, "Michael Londono Silva")


def test_a_report_without_prose_quotes_nothing(conn):
    assert bot._paragraph_about("Dynovia : Bielaszka, Kozioł", "Tomasz Bielaszka") == ""


def test_an_assist_only_counts_once_answered(conn):
    goal = db.goals_needing_assist(conn, "2026/27")[0]
    from dynovia import stats

    assert stats.assists_by_player(conn, "2026/27") == []
    db.store_assist(conn, goal["id"], "Filip Goleś")
    assert stats.assists_by_player(conn, "2026/27") == [("Filip Goleś", 1)]


def test_no_assist_is_an_answer_and_stops_the_asking(conn):
    goal = db.goals_needing_assist(conn, "2026/27")[0]
    bot.handle_callback(conn, f"a:{goal['id']}:x")
    assert goal["id"] not in {g["id"] for g in db.goals_needing_assist(conn, "2026/27")}


def test_nobody_assists_their_own_goal(conn):
    goal = db.goals_needing_assist(conn, "2026/27")[0]
    text, _ = bot.handle_command(conn, f"/asysta {goal['id']} {goal['scorer']}")
    assert "nie może asystować sam sobie" in text


def test_a_fresh_bot_skips_the_backlog(conn, monkeypatch):
    # Telegram keeps a day of updates. A rebuilt database must not answer all
    # of yesterday's commands at once.
    sent = []
    monkeypatch.setattr(
        bot.telegram,
        "get_updates",
        lambda offset: [
            {"update_id": 10, "message": message(text="/tabela")},
            {"update_id": 11, "message": message(text="/ostatni")},
        ],
    )
    monkeypatch.setattr(bot.telegram, "send", lambda *a, **k: sent.append(a))

    bot.poll(conn)
    assert sent == []
    assert db.get_setting(conn, bot.OFFSET_KEY) == "12"

    # And from then on it answers normally.
    bot.poll(conn)
    assert sent


def test_a_failed_push_is_reported_as_text_not_as_an_object(conn, monkeypatch):
    # /push once answered "Wysłałem push" while the server had refused it, and
    # then crashed on the reply. The command exists to catch silent failures,
    # so it must not be one itself.
    from dynovia.notify import webpush

    monkeypatch.setattr(webpush, "configured", lambda: True)
    monkeypatch.setattr(
        webpush, "send", lambda text: webpush.Problem("HTTP 403", expired=False)
    )
    text, _ = bot.handle_command(conn, "/push")
    assert isinstance(text, str)
    assert "403" in text


# --- protokol przyslany z telefonu ----------------------------------------

PROTOCOL = pathlib.Path("tests/fixtures/laczynaspilka/grom-2026-09-19.html")
GROM = ("2026/27", "dynovia dynow", "grom handzlowka")


def document(name="protokol.html", size=700_000) -> dict:
    return {"file_name": name, "file_size": size, "file_id": "abc"}


@pytest.fixture
def delivered(monkeypatch):
    """Whatever the bot decides to say back."""
    said = []
    monkeypatch.setattr(bot.telegram, "send", lambda text, **k: said.append(text))
    monkeypatch.setattr(
        bot.telegram, "get_file", lambda file_id: PROTOCOL.read_bytes()
    )
    return said


def test_a_protocol_sent_from_the_phone_is_imported(conn, delivered, monkeypatch):
    monkeypatch.setattr(bot.players, "load_roster", lambda path: REGISTRY)
    bot._dispatch(conn, {"message": message(document=document())})

    assert delivered[0].startswith("✅ Protokół")
    assert "Grom Handzlówka" in delivered[0]
    # The counts are the whole point of the reply - nobody reads the log from
    # a phone.
    assert "występów" in delivered[0]
    assert db.match_appearances(conn, db.match_ids(conn)[GROM])


def test_a_saved_shell_says_why_it_is_empty(conn, delivered, monkeypatch):
    # What Safari produces: the Angular shell, no squads anywhere in it.
    monkeypatch.setattr(bot.telegram, "get_file", lambda file_id: b"<html></html>")
    bot._dispatch(conn, {"message": message(document=document())})

    assert "Safari" in delivered[0]
    assert "Skrót" in delivered[0]


def test_the_name_decides_nothing_and_the_content_decides_everything(conn, monkeypatch):
    # A webarchive shared out of Safari on iOS arrives called "file", with no
    # extension. Routing on the name rejected exactly the thing it was built
    # to accept, so the first bytes are what counts now.
    monkeypatch.setattr(bot.players, "load_roster", lambda path: REGISTRY)
    said = []
    monkeypatch.setattr(bot.telegram, "send", lambda text, **k: said.append(text))
    monkeypatch.setattr(
        bot.telegram, "get_file", lambda file_id: webarchive(PROTOCOL.read_bytes())
    )
    bot._dispatch(conn, {"message": message(document=document("file"))})

    assert said[0].startswith("✅ Protokół")


def test_a_file_that_is_no_kind_of_page_is_named_as_such(conn, monkeypatch):
    said = []
    monkeypatch.setattr(bot.telegram, "send", lambda text, **k: said.append(text))
    monkeypatch.setattr(bot.telegram, "get_file", lambda f: bytes.fromhex("ffd8ffe000104a464946"))
    bot._dispatch(conn, {"message": message(document=document("zdjecie.jpg"))})

    assert said[0].startswith("❌")
    assert "Kompletna witryna" in said[0]


def test_an_oversized_file_is_refused_before_downloading(conn, monkeypatch):
    said, fetched = [], []
    monkeypatch.setattr(bot.telegram, "send", lambda text, **k: said.append(text))
    monkeypatch.setattr(bot.telegram, "get_file", lambda f: fetched.append(f) or b"")
    bot._dispatch(
        conn, {"message": message(document=document(size=bot.MAX_UPLOAD + 1))}
    )

    assert said[0].startswith("❌")
    assert fetched == []


def test_a_stranger_gets_nothing_done_and_no_answer(conn, delivered):
    # The reply would go to the owner's chat, but the work would still happen -
    # and this one writes to the database.
    bot._dispatch(conn, {"message": {"chat": {"id": 1}, "document": document()}})
    assert delivered == []
    assert not db.match_appearances(conn, db.match_ids(conn)[GROM])


def test_a_stranger_cannot_run_commands_either(conn, delivered):
    bot._dispatch(conn, {"message": {"chat": {"id": 1}, "text": "/tabela"}})
    assert delivered == []


def webarchive(page: bytes) -> bytes:
    """The shape Safari writes for Udostępnij → Opcje → Kompletna witryna."""
    return plistlib.dumps(
        {
            "WebMainResource": {
                "WebResourceData": page,
                "WebResourceMIMEType": "text/html",
                "WebResourceTextEncodingName": "UTF-8",
                "WebResourceURL": "https://www.laczynaspilka.pl/rozgrywki/mecz/x",
            },
            "WebSubresources": [],
        },
        fmt=plistlib.FMT_BINARY,
    )


def test_a_webarchive_is_read_from_its_main_resource(conn, monkeypatch):
    monkeypatch.setattr(bot.players, "load_roster", lambda path: REGISTRY)
    said = []
    monkeypatch.setattr(bot.telegram, "send", lambda text, **k: said.append(text))
    monkeypatch.setattr(
        bot.telegram, "get_file", lambda file_id: webarchive(PROTOCOL.read_bytes())
    )
    bot._dispatch(conn, {"message": message(document=document("strona.webarchive"))})

    assert said[0].startswith("✅ Protokół")
    assert db.match_appearances(conn, db.match_ids(conn)[GROM])


def test_a_webarchive_holding_only_the_shell_says_so(conn, monkeypatch):
    # The open question about this route: Safari may archive the server's empty
    # Angular shell rather than the rendered page. If it does, the owner has to
    # be told that, not left with silence.
    said = []
    monkeypatch.setattr(bot.telegram, "send", lambda text, **k: said.append(text))
    monkeypatch.setattr(
        bot.telegram, "get_file", lambda file_id: webarchive(b"<html><body></body></html>")
    )
    bot._dispatch(conn, {"message": message(document=document("strona.webarchive"))})

    assert "składów" in said[0]


def test_something_that_is_not_a_webarchive_is_named_as_such(conn, monkeypatch):
    said = []
    monkeypatch.setattr(bot.telegram, "send", lambda text, **k: said.append(text))
    monkeypatch.setattr(bot.telegram, "get_file", lambda file_id: b"zwykly tekst")
    bot._dispatch(conn, {"message": message(document=document("strona.webarchive"))})

    assert ".webarchive" in said[0]


# --- czekanie miedzy tikami -----------------------------------------------


@pytest.fixture
def waiting(monkeypatch, tmp_path):
    """dynovia.wait with its own database on disk.

    On disk rather than in memory because wait_for_message opens and closes
    its own connection, and the offset has to survive that.
    """
    from dynovia import wait

    real_connect = db.connect
    path = tmp_path / "wait.db"
    monkeypatch.setattr(wait.db, "connect", lambda: real_connect(path))
    return wait, real_connect(path)


def test_waiting_returns_the_moment_something_is_sent(waiting):
    wait, conn = waiting
    db.set_setting(conn, bot.OFFSET_KEY, "77")

    asked = []
    wait.telegram.get_updates = lambda offset, wait=0: (
        asked.append((offset, wait)) or [{"update_id": 78}]
    )
    assert wait.wait_for_message(600) is True
    # One long poll, opened where the bot left off, and out - not a second one
    # burning the rest of the ten minutes.
    assert asked == [(77, wait.SLICE)]

    # Nothing consumed: the offset is untouched, so the tick this wakes up
    # still finds the message waiting for it.
    assert db.get_setting(conn, bot.OFFSET_KEY) == "77"


def test_waiting_gives_up_when_the_time_runs_out(waiting, monkeypatch):
    wait, _ = waiting
    monkeypatch.setattr(wait, "SLICE", 0)
    monkeypatch.setattr(wait.telegram, "get_updates", lambda offset, wait=0: [])
    assert wait.wait_for_message(0.05) is False


def test_a_broken_long_poll_does_not_end_the_wait_early(waiting, monkeypatch):
    # Telegram blips. The loop keeps its own deadline rather than turning one
    # failed request into an immediate extra tick.
    wait, _ = waiting
    monkeypatch.setattr(wait, "SLICE", 0)

    def broken(offset, wait=0):
        raise RuntimeError("502")

    monkeypatch.setattr(wait.telegram, "get_updates", broken)
    assert wait.wait_for_message(0.05) is False


def test_a_file_from_the_phone_reaches_the_app_in_the_same_tick(conn, monkeypatch):
    """A protocol arrives through bot.poll. If the export has already run by
    then, the app serves JSON without it until the next tick - ten more
    minutes for data that is already in the database."""
    from dynovia import run

    order = []
    real_connect = db.connect
    monkeypatch.setattr(run, "collect", lambda *a, **k: [])
    monkeypatch.setattr(run.differ, "due_reminders", lambda *a, **k: [])
    monkeypatch.setattr(run.db, "connect", lambda: real_connect(":memory:"))
    monkeypatch.setattr(run.bot, "poll", lambda c: order.append("poll"))
    monkeypatch.setattr(run.export, "write", lambda c: order.append("export"))

    run.main([])
    assert order == ["poll", "export"]
