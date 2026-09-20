"""Normalized shapes every scraper returns.

None of these carry a `source` field. The source name sits once on ScrapeResult
and the SQLite writer stamps it onto every row it inserts, so the database keeps
the "every record has a source" rule without repeating the string in Python.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

MatchStatus = Literal["scheduled", "live", "finished", "postponed"]
GoalType = Literal["normal", "penalty", "own"]
CardColor = Literal["yellow", "second_yellow", "red"]

# (date, normalized home, normalized away) - the natural key that joins the same
# match across sources. Every source has its own ids, so ids cannot do this job.
MatchKey = tuple[dt.date, str, str]

# Club-type prefixes that some sources print and others drop.
_TEAM_NOISE = re.compile(r"\b(ks|lks|uks|mks|gks|kks|zks|ludowy|klub|sportowy)\b")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# NFKD does not decompose the Polish stroked l, so it needs its own mapping.
_STROKED_L = str.maketrans({"ł": "l", "Ł": "l"})


def normalize_team(name: str) -> str:
    """'LKS Dynovia Dynow' and 'Dynovia Dynow' both become 'dynovia dynow'."""
    folded = name.casefold().translate(_STROKED_L)
    decomposed = unicodedata.normalize("NFKD", folded)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _NON_ALNUM.sub(" ", _TEAM_NOISE.sub(" ", ascii_only)).strip()


def normalize_player(name: str) -> str:
    """Player name reduced to a comparison key. Alias resolution proper ('M. Bak'
    vs 'Bak Mateusz') lands in merge.py in phase 3 - this is only the cheap part."""
    folded = name.casefold().translate(_STROKED_L)
    decomposed = unicodedata.normalize("NFKD", folded)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _NON_ALNUM.sub(" ", ascii_only).strip()


@dataclass(frozen=True, slots=True)
class MatchData:
    # Both optional: 90minut lists late rounds with no date and no kickoff yet.
    date: dt.date | None
    time: dt.time | None
    competition: str
    home: str
    away: str
    round: int | None = None
    home_score: int | None = None
    away_score: int | None = None
    status: MatchStatus = "scheduled"
    venue: str | None = None
    external_id: str | None = None

    @property
    def key(self) -> MatchKey:
        return (self.date, normalize_team(self.home), normalize_team(self.away))


@dataclass(frozen=True, slots=True)
class AppearanceData:
    match: MatchKey
    player: str
    started: bool
    minute_in: int | None = None
    minute_out: int | None = None


@dataclass(frozen=True, slots=True)
class GoalData:
    match: MatchKey
    player: str
    minute: int | None = None
    type: GoalType = "normal"


@dataclass(frozen=True, slots=True)
class CardData:
    match: MatchKey
    player: str
    color: CardColor
    minute: int | None = None


@dataclass(frozen=True, slots=True)
class TableRow:
    position: int
    team: str
    played: int
    points: int
    goals_for: int
    goals_against: int


@dataclass(frozen=True, slots=True)
class MatchReport:
    """Raw prose of a match report. Assists are mined from this in phase 5."""

    match: MatchKey
    url: str
    text: str
