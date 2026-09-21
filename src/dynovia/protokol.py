"""Importing PZPN match protocols that were saved by hand.

laczynaspilka.pl is an Angular app whose data sits behind a Keycloak-gated API;
the server serves an empty shell and the public client cannot obtain a token, so
there is nothing to scrape. What there is: a page the owner can save with
Ctrl+S, which contains the richest record of a match anywhere - both lineups
with shirt numbers, every substitution with its minute, and the cards.

Drop such a file into protokoly/ and the next run reads it. Importing is
idempotent, so the file can stay there; nothing is deleted behind the owner's
back.

Event types are only distinguishable by the colour of an inline icon, which is
as fragile as it sounds - see _icon_kind.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from selectolax.parser import HTMLParser

from dynovia import db, merge
from dynovia.models import (
    AppearanceData,
    CardData,
    GoalData,
    MatchData,
    MatchKey,
    normalize_team,
    season_for,
)

log = logging.getLogger(__name__)

SOURCE = "laczynaspilka"
FULL_TIME = 90

STARTERS = "Skład wyjściowy"
SUBSTITUTES = "Skład rezerwowy"

# The protocol marks goalkeeper, captain and youth player after the name.
_MARKERS = re.compile(r"\s*\([BCM]\)")
_MINUTE = re.compile(r"(\d+)")
_FILL = re.compile(r'fill="(#[0-9A-Fa-f]{6})"')


@dataclass(slots=True)
class Protocol:
    home: str = ""
    away: str = ""
    squads: dict[str, list[tuple[str, bool]]] = field(default_factory=dict)
    """{team: [(player, started)]} - everyone listed, played or not."""
    events: list[tuple[int, str, str]] = field(default_factory=list)
    """(minute, player, kind) with kind in goal|yellow|red|on|off."""


def _icon_kind(node) -> str | None:
    """What an event is, read off its icon.

    There is nothing else to go on: no class, no label, no text. A football
    outline is a goal, a coloured rectangle is a card, and the arrows for
    coming on and off differ only in colour. An unknown icon is skipped rather
    than guessed at, and logged so the mapping can be extended.
    """
    icon = node.css_first("svg-icon")
    if icon is None:
        return None
    svg = icon.html or ""
    if "football" in svg:
        return "goal"
    fills = _FILL.findall(svg)
    colour = next((f.upper() for f in fills if f.upper() != "#FFFFFF"), None)
    if colour is None:
        return None
    if "<polygon" in svg:
        red, green = int(colour[1:3], 16), int(colour[3:5], 16)
        return "red" if red > 0x90 and green < 0x60 else "yellow"
    return {"#F26C36": "off", "#8BF236": "on"}.get(colour)


def parse_protocol(html: str) -> Protocol:
    tree = HTMLParser(html)
    for node in tree.css("script, style"):
        node.decompose()

    protocol = Protocol()
    teams: list[str] = []
    team = section = None
    for node in tree.root.traverse(include_text=False):
        classes = node.attributes.get("class") or ""
        if node.tag == "app-headline-block":
            # The full name and its three-letter abbreviation are separate
            # divs, one shown per breakpoint; taking the link's text glues them
            # into "Dynovia DynówDYN", which matches no team anywhere.
            name = node.css_first(".squad-name__name--full")
            if name is None:
                team = None
                continue
            team = re.sub(r"\s+", " ", name.text(strip=True)).strip()
            section = None
            if team not in teams:
                teams.append(team)
            protocol.squads.setdefault(team, [])
        elif node.tag == "app-headline-section":
            section = node.text(strip=True)
        elif "player-cell" in classes and team and section in (STARTERS, SUBSTITUTES):
            name_node = node.css_first(".font-20")
            if name_node is None:
                continue
            name = re.sub(r"\s+", " ", _MARKERS.sub("", name_node.text(strip=True)))
            entry = (name.strip(), section == STARTERS)
            # Every player is rendered twice, once for desktop and once for
            # mobile; both carry the same data.
            if entry not in protocol.squads[team]:
                protocol.squads[team].append(entry)

    if len(teams) >= 2:
        protocol.home, protocol.away = teams[0], teams[1]

    for block in tree.css("div.block"):
        title = block.css_first(".content_title")
        minute_cell = block.css_first(".time")
        kind = _icon_kind(block)
        if title is None or kind is None:
            continue
        minute = _MINUTE.search(minute_cell.text(strip=True) if minute_cell else "")
        protocol.events.append(
            (
                int(minute.group(1)) if minute else 0,
                re.sub(r"\s+", " ", title.text(strip=True)).strip(),
                kind,
            )
        )
    return protocol


def our_squad(protocol: Protocol) -> dict[str, bool]:
    """{player: started} for Dynovia only. The protocol lists both teams, and
    the opponents are not in our roster - importing them would raise a question
    about every single one."""
    for team, squad in protocol.squads.items():
        if "dynovia" in normalize_team(team):
            return dict(squad)
    return {}


def to_records(
    protocol: Protocol, match: MatchKey
) -> tuple[list[AppearanceData], list[GoalData], list[CardData]]:
    squad = our_squad(protocol)
    on = {name: minute for minute, name, kind in protocol.events if kind == "on"}
    off = {name: minute for minute, name, kind in protocol.events if kind == "off"}

    appearances = []
    for name, started in squad.items():
        if not started and name not in on:
            continue  # an unused substitute did not play
        appearances.append(
            AppearanceData(
                match=match,
                player=name,
                started=started,
                minute_in=0 if started else on[name],
                minute_out=off.get(name, FULL_TIME),
            )
        )

    goals = [
        GoalData(match=match, player=name, minute=minute)
        for minute, name, kind in protocol.events
        if kind == "goal" and name in squad
    ]
    cards = [
        CardData(match=match, player=name, minute=minute, color=kind)
        for minute, name, kind in protocol.events
        if kind in ("yellow", "red") and name in squad
    ]
    return appearances, goals, cards


def find_match(conn: sqlite3.Connection, protocol: Protocol) -> MatchKey | None:
    """Which stored match this protocol describes.

    The page carries no date, so it is identified by its two teams. The exact
    order is tried first and the reverse only as a fallback: the same pairing
    occurs twice a season, once each way, and matching the unordered pair alone
    could file an autumn protocol against the spring fixture.
    """
    home, away = normalize_team(protocol.home), normalize_team(protocol.away)
    known = list(db.stored_matches(conn))
    for wanted in ((home, away), (away, home)):
        for season, stored_home, stored_away in known:
            if (stored_home, stored_away) == wanted:
                return (season, stored_home, stored_away)
    return None


def import_directory(
    conn: sqlite3.Connection, directory: Path, registry: dict[str, str]
) -> list[tuple[int, merge.Conflict]]:
    if not directory.is_dir():
        return []
    conflicts: list[tuple[int, merge.Conflict]] = []
    for path in sorted(directory.glob("*.html")):
        try:
            conflicts += import_file(conn, path, registry)
        except Exception:  # noqa: BLE001 - a bad file must not stop the run
            log.exception("protokol %s: import failed", path.name)
    return conflicts


def import_file(
    conn: sqlite3.Connection, path: Path, registry: dict[str, str]
) -> list[tuple[int, merge.Conflict]]:
    protocol = parse_protocol(path.read_text(encoding="utf-8", errors="replace"))
    match = find_match(conn, protocol)
    if match is None:
        log.warning(
            "protokol %s: %s - %s nie pasuje do zadnego meczu w bazie",
            path.name,
            protocol.home or "?",
            protocol.away or "?",
        )
        return []

    appearances, goals, cards = to_records(protocol, match)
    log.info(
        "protokol %s: %d wystepow, %d bramek, %d kartek",
        path.name,
        len(appearances),
        len(goals),
        len(cards),
    )
    return (
        db.store_lineups(conn, appearances, SOURCE, registry)
        + db.store_goals(conn, goals, SOURCE, registry)
        + db.store_cards(conn, cards, SOURCE, registry)
    )


SCHEDULE_FILE = "terminarz.txt"

_SCHEDULE_DATE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")
_SCHEDULE_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")
_SCHEDULE_SCORE = re.compile(r"^(\d+):(\d+)$")


def parse_schedule(text: str) -> list["MatchData"]:
    """The fixture list as laczynaspilka renders it, pasted into a text file.

    Six fields per match once blank lines are gone: date, hosts, then either a
    kickoff, a score or "-:-", then guests, competition and status. The PZPN
    site cannot be fetched, so this is how its schedule gets in - and being the
    PZPN record, whatever it says outranks every scraper.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    matches = []
    index = 0
    while index + 5 < len(lines):
        date_match = _SCHEDULE_DATE.match(lines[index])
        if date_match is None:
            index += 1
            continue
        day, month, year = date_match.groups()
        date = dt.date(int(year), int(month), int(day))
        home, middle, away, competition = lines[index + 1 : index + 5]

        time = score = None
        if found := _SCHEDULE_TIME.match(middle):
            time = dt.time(int(found.group(1)), int(found.group(2)))
        elif found := _SCHEDULE_SCORE.match(middle):
            score = (int(found.group(1)), int(found.group(2)))

        matches.append(
            MatchData(
                season=season_for(date),
                date=date,
                time=time,
                competition=competition,
                home=home,
                away=away,
                home_score=score[0] if score else None,
                away_score=score[1] if score else None,
                status="finished" if score else "scheduled",
            )
        )
        index += 6
    return matches


def import_schedule(
    conn: sqlite3.Connection, path: Path, fetched_at: dt.datetime
) -> list[tuple[int, merge.Conflict]]:
    """Re-imported only when the file actually changed.

    Storing it unconditionally restamped every fetched_at on every run, so the
    database and the exported JSON differed every time and Actions committed
    on every tick - which is exactly what "commit only when changed" was meant
    to prevent.
    """
    if not path.is_file():
        return []

    text = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if db.get_setting(conn, "schedule_hash") == digest:
        return []
    db.set_setting(conn, "schedule_hash", digest)

    matches = parse_schedule(text)
    if not matches:
        log.warning("terminarz PZPN %s: nic nie sparsowano", path.name)
        return []
    log.info("terminarz PZPN: %d meczow", len(matches))
    return db.store_matches(conn, matches, SOURCE, fetched_at)
