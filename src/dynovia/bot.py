"""Bot commands, answered during the cron run.

There is no webhook and no server, so updates are collected with getUpdates on
each tick. A command therefore waits up to one cron interval for its answer -
the price of having no machine to run on. The offset is stored in the database
and advanced per update, so a crash mid-batch replays nothing.

Buttons are the point of /konflikty: an answer there is recorded for good, so
the same question is never asked twice.
"""

from __future__ import annotations

import datetime as dt
import logging

from dynovia import db, differ, players, stats
from dynovia.config import ROSTER_PATH
from dynovia.models import season_for
from dynovia.notify import telegram

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
/konflikty - nierozstrzygnięte rozbieżności"""


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
        f"Gole: {summary['goals']}\n"
        f"Kartki: 🟨 {cards.get('yellow', 0)}  🟥 {cards.get('red', 0)}"
    ), None


def cmd_sources(conn, args: str) -> tuple[str, list | None]:
    rows = db.source_status(conn)
    if not rows:
        return "Żadne źródło jeszcze nie zadziałało.", None
    lines = []
    for row in rows:
        last = dt.datetime.fromisoformat(row["last"]).astimezone(differ.WARSAW)
        lines.append(f"{row['source']:<16} {row['matches']:>3} mecz. {last:%d.%m %H:%M}")
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


COMMANDS = {
    "nastepny": cmd_next,
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


def poll(conn) -> None:
    """Answer whatever was sent since the last run."""
    offset = int(db.get_setting(conn, OFFSET_KEY) or 0)
    try:
        updates = telegram.get_updates(offset)
    except Exception:  # noqa: BLE001 - a dead bot must not stop the scraping
        log.exception("getUpdates failed")
        return

    for update in updates:
        # Advanced before handling: a command that crashes must not be replayed
        # on every run for ever after.
        db.set_setting(conn, OFFSET_KEY, update["update_id"] + 1)
        try:
            _dispatch(conn, update)
        except Exception:  # noqa: BLE001
            log.exception("update %s failed", update.get("update_id"))


def _dispatch(conn, update: dict) -> None:
    if "callback_query" in update:
        query = update["callback_query"]
        answer = handle_callback(conn, query.get("data", ""))
        telegram.answer_callback(query["id"], answer[:190])
        telegram.send(answer)
        return

    message = update.get("message") or update.get("edited_message") or {}
    text = (message.get("text") or "").strip()
    if not text.startswith("/"):
        return
    reply, buttons = handle_command(conn, text)
    telegram.send(reply, buttons=buttons, as_html=reply.startswith("<pre>"))
