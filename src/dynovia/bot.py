"""Bot commands, answered during the cron run.

There is no webhook and no server, so updates are collected with getUpdates on
each tick. A command therefore waits up to one cron interval for its answer -
the price of having no machine to run on. The offset is stored in the database
and advanced per update, so a crash mid-batch replays nothing.

Buttons are the point of /konflikty: an answer there is recorded for good, so
the same question is never asked twice.
"""

from __future__ import annotations

import base64
import datetime as dt
import gzip
import io
import logging
import re

from dynovia import db, differ, players, protokol, stats
from dynovia.config import ROSTER_PATH, TELEGRAM_CHAT_ID
from dynovia.models import normalize_player, season_for
from dynovia.notify import telegram, webpush

log = logging.getLogger(__name__)

OFFSET_KEY = "telegram_offset"

HELP = """Komendy:
/nastepny - najbliższy mecz
/ostatni - ostatni wynik ze strzelcami
/terminarz - najbliższe kolejki
/tabela - tabela ligowa
/strzelcy - klasyfikacja strzelców
/zawodnik <nazwisko> - statystyki zawodnika
/zrodla - stan każdego źródła
/asysty - przypisz asysty do bramek
/asysta <nr> <nazwisko> - to samo z palca
/konflikty - nierozstrzygnięte rozbieżności
/push - sprawdź, czy powiadomienia na telefon działają"""


def _season(conn) -> str:
    return season_for(dt.date.today())


def _sorted_matches(conn) -> list:
    matches = [m for m in db.stored_matches(conn).values() if m.date]
    return sorted(matches, key=lambda m: (m.date, m.time or dt.time()))


def cmd_next(conn, args: str) -> tuple[str, list | None]:
    now = dt.datetime.now(dt.UTC)
    upcoming = [
        m
        for m in _sorted_matches(conn)
        if m.status != "finished" and (differ.kickoff(m) or now) >= now
    ]
    if not upcoming:
        return "Brak zaplanowanych meczów.", None

    match = upcoming[0]
    start = differ.kickoff(match)
    when = f"{match.date:%d.%m}" + (f" o {match.time:%H:%M}" if match.time else "")
    lines = [f"⚽ {match.home} – {match.away}", f"{when}  ·  {match.competition}"]
    if start:
        left = start - now
        hours = int(left.total_seconds() // 3600)
        lines.append(f"Zostało: {hours // 24} dni {hours % 24} godz.")
    return "\n".join(lines), None


def cmd_last(conn, args: str) -> tuple[str, list | None]:
    played = [m for m in _sorted_matches(conn) if m.status == "finished"]
    if not played:
        return "Brak rozegranych meczów.", None

    match = played[-1]
    lines = [
        f"🏁 {match.home} {match.home_score}–{match.away_score} {match.away}",
        f"{match.date:%d.%m}  ·  {match.competition}",
    ]
    scorers = _scorers(conn, match)
    if scorers:
        lines.append("")
        lines += scorers
    return "\n".join(lines), None


def _scorers(conn, match) -> list[str]:
    """Goals for one match, taken from the most trusted source that saw any -
    never mixed, or a 4-1 ends up with seven scorers."""
    rows = conn.execute(
        "SELECT g.source, g.minute, p.name FROM goals g"
        " JOIN players p ON p.id = g.player_id"
        " JOIN matches m ON m.id = g.match_id"
        " WHERE m.season = ? AND m.home_key = ? AND m.away_key = ?",
        (match.season, *match.key[1:]),
    ).fetchall()
    if not rows:
        return []
    from dynovia.merge import GOAL_TRUST

    best = min(
        {row["source"] for row in rows},
        key=lambda s: GOAL_TRUST.index(s) if s in GOAL_TRUST else len(GOAL_TRUST),
    )
    goals = [row for row in rows if row["source"] == best]
    goals.sort(key=lambda row: row["minute"] if row["minute"] is not None else 999)
    return [
        f"⚽ {row['name']}" + (f" {row['minute']}'" if row["minute"] else "")
        for row in goals
    ]


def cmd_schedule(conn, args: str) -> tuple[str, list | None]:
    now = dt.date.today()
    upcoming = [m for m in _sorted_matches(conn) if m.date >= now][:8]
    if not upcoming:
        return "Brak zaplanowanych meczów.", None
    lines = [
        f"{m.date:%d.%m} {m.time:%H:%M}  {m.home} – {m.away}"
        if m.time
        else f"{m.date:%d.%m}        {m.home} – {m.away}"
        for m in upcoming
    ]
    return telegram.pre("\n".join(lines)), None


def cmd_table(conn, args: str) -> tuple[str, list | None]:
    rows = db.read_table(conn, _season(conn))
    if not rows:
        return "Tabela jeszcze nie pobrana.", None
    lines = [f"{'':>2}  {'drużyna':<24} {'M':>2} {'Pkt':>3} {'+/-':>4}"]
    for row in rows:
        lines.append(
            f"{row['position']:>2}. {row['team'][:24]:<24}"
            f" {row['played']:>2} {row['points']:>3} {row['goal_difference']:>+4}"
        )
    return telegram.pre("\n".join(lines)), None


def cmd_scorers(conn, args: str) -> tuple[str, list | None]:
    tally = stats.goals_by_player(conn, _season(conn))
    if not tally:
        return "Jeszcze nikt nie strzelił.", None
    lines = [f"{count:>2}  {name}" for name, count in tally]
    return telegram.pre("\n".join(lines)), None


def cmd_player(conn, args: str) -> tuple[str, list | None]:
    if not args.strip():
        return "Podaj nazwisko, np. /zawodnik Kłoda", None

    registry = players.load_roster(ROSTER_PATH)
    name, candidates = players.resolve(args.strip(), registry)
    if name is None:
        if candidates:
            return "Kogo dokładnie? " + ", ".join(candidates), None
        return f"Nie znam nikogo takiego: {args.strip()}", None

    summary = stats.player_summary(conn, _season(conn), name)
    if not summary:
        return f"{name}: brak danych w tym sezonie.", None
    cards = summary["cards"]
    return (
        f"{summary['name']}\n"
        f"Mecze: {summary['played']}\n"
        f"Minuty: {summary['minutes']} (tylko z protokołów PZPN)\n"
        f"Gole: {summary['goals']}   Asysty: {summary['assists']}\n"
        f"Kartki: 🟨 {cards.get('yellow', 0)}  🟥 {cards.get('red', 0)}"
    ), None


def cmd_sources(conn, args: str) -> tuple[str, list | None]:
    rows = db.source_status(conn)
    if not rows:
        return "Żadne źródło jeszcze nie zadziałało.", None
    lines = []
    for row in rows:
        last = dt.datetime.fromisoformat(row["last"]).astimezone(differ.WARSAW)
        failures = int(db.get_setting(conn, f"failures:{row['source']}") or 0)
        alarm = f"  ⚠️x{failures}" if failures else ""
        lines.append(
            f"{row['source']:<16} {row['matches']:>3} mecz. {last:%d.%m %H:%M}{alarm}"
        )
    return telegram.pre("\n".join(lines)), None


def cmd_conflicts(conn, args: str) -> tuple[str, list | None]:
    rows = db.open_conflicts(conn)
    if not rows:
        return "Brak nierozstrzygniętych rozbieżności.", None

    row = rows[0]  # one at a time: each answer needs its own keyboard
    header = f"{row['home']} – {row['away']}"
    if row["field"] == "zawodnik":
        candidates = [c.strip() for c in (row["value_b"] or "").split(",") if c.strip()]
        buttons = [[(name, f"p:{row['id']}:{index}")] for index, name in enumerate(candidates)]
        text = f"❓ {header}\nKto to jest „{row['value_a']}” ({row['source_a']})?"
        return text, buttons or None

    buttons = [
        [(f"{row['source_a']}: {row['value_a']}", f"c:{row['id']}:a")],
        [(f"{row['source_b']}: {row['value_b']}", f"c:{row['id']}:b")],
    ]
    remaining = f"\n\nPozostało: {len(rows) - 1}" if len(rows) > 1 else ""
    return f"⚠️ {header}\nPole: {row['field']}{remaining}", buttons


_NOT_PROSE = re.compile(r"^\s*(Dynovia\s*:|Bramk|Na\s+zmiany)", re.I)


def _paragraph_about(report: str, scorer: str) -> str:
    """The sentences of the report that name the scorer.

    This is the whole manual variant: the club describes its goals in prose,
    so the owner gets the prose and decides. Nothing here guesses who passed.
    """
    if not report:
        return ""
    # Any part of the name, matched as a prefix: Polish inflects it ("Michaela
    # Londono"), and the last word is not reliably the surname either.
    parts = [word for word in normalize_player(scorer).split() if len(word) >= 4]
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n", report)]
    hits = []
    for sentence in sentences:
        # The lineup and the goal line name the scorer too, and quoting those
        # back tells the owner nothing he did not already see.
        if _NOT_PROSE.match(sentence) or len(sentence.split()) < 6:
            continue
        words = normalize_player(sentence).split()
        if any(any(word.startswith(part) for word in words) for part in parts):
            hits.append(sentence)
    return " ".join(hits[:2])[:600]


def assist_question(conn, goal) -> tuple[str, list]:
    squad = [n for n in db.match_squad(conn, goal["match_id"]) if n != goal["scorer"]]
    minute = f" {goal['minute']}'" if goal["minute"] else ""
    lines = [
        "🅰️ Kto asystował?",
        f"{goal['home']} – {goal['away']} ({goal['date']})",
        f"⚽ {goal['scorer']}{minute}   [nr {goal['id']}]",
    ]
    paragraph = _paragraph_about(db.report_for(conn, goal["match_id"]), goal["scorer"])
    if paragraph:
        lines += ["", f"„{paragraph}”"]

    if not squad:
        # No lineup stored for that match, so there is nobody to put on a
        # button. The typed command exists for exactly this case.
        lines.append(f"\nBrak składu tego meczu - wpisz /asysta {goal['id']} <nazwisko>")
        return "\n".join(lines), [[("Nikt / nie wiem", f"a:{goal['id']}:x")]]

    buttons = [
        [(name, f"a:{goal['id']}:{index}") for index, name in pair]
        for pair in _in_pairs(list(enumerate(squad)))
    ]
    buttons.append([("Nikt / nie wiem", f"a:{goal['id']}:x")])
    return "\n".join(lines), buttons


def _in_pairs(items: list) -> list[list]:
    return [items[i : i + 2] for i in range(0, len(items), 2)]


def cmd_assists(conn, args: str) -> tuple[str, list | None]:
    pending = db.goals_needing_assist(conn, _season(conn))
    if not pending:
        tally = stats.assists_by_player(conn, _season(conn))
        if not tally:
            return "Wszystkie bramki opisane, asyst jeszcze nie ma.", None
        return telegram.pre("\n".join(f"{n:>2}  {name}" for name, n in tally)), None
    return assist_question(conn, pending[0])


def cmd_assist(conn, args: str) -> tuple[str, list | None]:
    number, _, name = args.strip().partition(" ")
    if not number.isdigit() or not name.strip():
        return "Użycie: /asysta <nr bramki> <nazwisko>   (nr pokazuje /asysty)", None

    registry = players.load_roster(ROSTER_PATH)
    player, candidates = players.resolve(name.strip(), registry)
    if player is None:
        return ("Kogo dokładnie? " + ", ".join(candidates)) if candidates else (
            f"Nie znam nikogo takiego: {name.strip()}"
        ), None
    scorer = conn.execute(
        "SELECT p.name FROM goals g JOIN players p ON p.id = g.player_id"
        " WHERE g.id = ?",
        (int(number),),
    ).fetchone()
    if scorer is None:
        return f"Nie ma bramki nr {number}.", None
    if scorer["name"] == player:
        return f"{player} strzelił tę bramkę - nie może asystować sam sobie.", None
    db.store_assist(conn, int(number), player)
    return f"Zapisane: asysta {player} przy bramce nr {number}.", None


def cmd_push(conn, args: str) -> tuple[str, list | None]:
    """Worth having permanently: iOS drops a push subscription after a few
    weeks of not opening the app, and it does so silently. This is the only way
    to find out before a match instead of after one."""
    if not webpush.configured():
        return (
            "Push nieskonfigurowany - brak VAPID_PRIVATE_KEY albo "
            "PUSH_SUBSCRIPTION w sekretach.",
            None,
        )
    problem = webpush.send("Dynovia Alerts\nTest - jeśli to widzisz, push działa.")
    if problem:
        return problem.message, None
    return "Wysłałem push. Sprawdź ekran blokady telefonu.", None


COMMANDS = {
    "nastepny": cmd_next,
    "push": cmd_push,
    "asysty": cmd_assists,
    "asysta": cmd_assist,
    "ostatni": cmd_last,
    "terminarz": cmd_schedule,
    "tabela": cmd_table,
    "strzelcy": cmd_scorers,
    "zawodnik": cmd_player,
    "zrodla": cmd_sources,
    "konflikty": cmd_conflicts,
}


def handle_command(conn, text: str) -> tuple[str, list | None]:
    command, _, args = text.lstrip("/").partition(" ")
    command = command.split("@")[0].lower()
    handler = COMMANDS.get(command)
    if handler is None:
        return HELP, None
    return handler(conn, args)


def handle_callback(conn, data: str) -> str:
    kind, _, rest = data.partition(":")
    conflict_id, _, choice = rest.partition(":")
    if kind not in ("c", "p", "a"):
        return "Nie rozumiem."
    if kind == "a":
        return _answer_assist(conn, int(conflict_id), choice)

    row = conn.execute(
        "SELECT * FROM conflicts WHERE id = ?", (int(conflict_id),)
    ).fetchone()
    if row is None:
        return "Nie ma już takiego pytania."

    if kind == "p":
        candidates = [c.strip() for c in (row["value_b"] or "").split(",") if c.strip()]
        index = int(choice)
        if index >= len(candidates):
            return "Nie rozumiem wyboru."
        db.pin_player(conn, row["id"], candidates[index])
        return f"Zapisane: „{row['value_a']}” to {candidates[index]}."

    value = row["value_a"] if choice == "a" else row["value_b"]
    db.choose_conflict(conn, row["id"], value)
    return f"Zapisane: {row['field']} = {value}."


def _answer_assist(conn, goal_id: int, choice: str) -> str:
    goal = conn.execute(
        "SELECT g.match_id, p.name AS scorer FROM goals g"
        " JOIN players p ON p.id = g.player_id WHERE g.id = ?",
        (goal_id,),
    ).fetchone()
    if goal is None:
        return "Nie ma już takiej bramki."
    if choice == "x":
        db.store_assist(conn, goal_id, None)
        return f"Zapisane: bramka {goal['scorer']} bez asysty."

    squad = [n for n in db.match_squad(conn, goal["match_id"]) if n != goal["scorer"]]
    index = int(choice)
    if index >= len(squad):
        return "Nie rozumiem wyboru."
    db.store_assist(conn, goal_id, squad[index])
    return f"Zapisane: {squad[index]} asystował przy bramce {goal['scorer']}."


def poll(conn) -> None:
    """Answer whatever was sent since the last run."""
    stored = db.get_setting(conn, OFFSET_KEY)
    try:
        updates = telegram.get_updates(int(stored or 0))
    except Exception:  # noqa: BLE001 - a dead bot must not stop the scraping
        log.exception("getUpdates failed")
        return

    if stored is None and updates:
        # First poll on a fresh database. Telegram keeps a day of updates, and
        # answering all of yesterday's commands at once is noise, not service.
        db.set_setting(conn, OFFSET_KEY, updates[-1]["update_id"] + 1)
        log.info("pominieto %d zaleglych komend przy pierwszym uruchomieniu", len(updates))
        return

    for update in updates:
        # Advanced before handling: a command that crashes must not be replayed
        # on every run for ever after.
        db.set_setting(conn, OFFSET_KEY, update["update_id"] + 1)
        try:
            _dispatch(conn, update)
        except Exception:  # noqa: BLE001
            log.exception("update %s failed", update.get("update_id"))


MAX_UPLOAD = 5 * 1024 * 1024
"""A saved protocol page is under a megabyte. Anything much larger is not one,
and there is no reason to pull it down to find that out."""

MAX_UNPACKED = 20 * 1024 * 1024
"""Ceiling on what a .b64 may expand to. Only the owner can send anything here,
so this is not a defence against an attacker - it is a defence against a file
that turns out not to be what it looked like."""


def _from_owner(update: dict) -> bool:
    """This bot serves exactly one person.

    Without this anyone who finds the bot can run its commands - the answer
    goes to the owner's chat, but the work still happens, and /asysta writes to
    the database. Once files are accepted too, a stranger could put a protocol
    of their choosing into it.
    """
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or update.get("callback_query", {}).get(
        "message", {}
    ).get("chat", {})
    return bool(TELEGRAM_CHAT_ID) and str(chat.get("id")) == str(TELEGRAM_CHAT_ID)


def _dispatch(conn, update: dict) -> None:
    if not _from_owner(update):
        log.warning("zignorowano update spoza czatu wlasciciela")
        return

    if "callback_query" in update:
        query = update["callback_query"]
        answer = handle_callback(conn, query.get("data", ""))
        telegram.answer_callback(query["id"], answer[:190])
        telegram.send(answer)
        return

    message = update.get("message") or update.get("edited_message") or {}
    if document := message.get("document"):
        telegram.send(_receive_protocol(conn, document))
        return

    text = (message.get("text") or "").strip()
    if not text.startswith("/"):
        return
    reply, buttons = handle_command(conn, text)
    telegram.send(reply, buttons=buttons, as_html=reply.startswith("<pre>"))


def _unpack(raw: bytes) -> bytes:
    """base64 of a gzipped page, back into the page.

    Read with a ceiling rather than all at once, so a file that expands beyond
    all reason is refused instead of unpacked first and judged afterwards.
    """
    try:
        packed = base64.b64decode(b"".join(raw.split()), validate=True)
        with gzip.GzipFile(fileobj=io.BytesIO(packed)) as unzipped:
            page = unzipped.read(MAX_UNPACKED + 1)
    except Exception as problem:  # noqa: BLE001 - every failure reads the same
        raise ValueError("nie udało się rozpakować, to nie jest plik ze Skrótu") from problem
    if len(page) > MAX_UNPACKED:
        raise ValueError("po rozpakowaniu jest absurdalnie duży")
    return page


def _receive_protocol(conn, document: dict) -> str:
    """A protocol page sent from the phone, via the Shortcut - see web/app.js.

    The reply is the only feedback there is: nobody reads the Actions log from
    a phone, so every outcome has to say which one it was, and an empty page
    has to name the likely cause rather than just failing.
    """
    name = document.get("file_name") or "plik"
    if not name.lower().endswith((".html", ".htm", ".b64")):
        return f"❌ {name}: przyjmuję tylko .b64 ze Skrótu albo .html z Ctrl+S."
    if (document.get("file_size") or 0) > MAX_UPLOAD:
        return f"❌ {name}: za duży, protokół waży poniżej megabajta."

    raw = telegram.get_file(document["file_id"])
    if name.lower().endswith(".b64"):
        # iOS refuses to carry 657 kB out of the JavaScript action - see the
        # instructions in web/app.js - so the Shortcut gzips the page first.
        # What the parser gets is still byte for byte the page itself.
        try:
            raw = _unpack(raw)
        except ValueError as problem:
            return f"❌ {name}: {problem}"

    result = protokol.import_html(
        conn, name, raw.decode("utf-8", errors="replace"), players.load_roster(ROSTER_PATH)
    )
    if result.empty:
        return (
            f"❌ {name}: nie ma w tym pliku żadnych składów.\n"
            "Tak wygląda strona zapisana przez Safari zamiast przez Skrót - "
            "Safari zapisuje pustą skorupę, bez danych meczu."
        )
    if result.match is None:
        return (
            f"❌ {name}: {result.home} – {result.away} nie pasuje do żadnego "
            "meczu w bazie."
        )
    return (
        f"✅ Protokół: {result.home} – {result.away}\n"
        f"{result.appearances} występów, {result.goals} bramek, "
        f"{result.cards} kartek."
    )
