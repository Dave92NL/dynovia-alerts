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

# (season, normalized home, normalized away) - the natural key that joins the same
# match across sources. Every source has its own ids, so ids cannot do this job.
#
# Not the date: late rounds are published without one and gain it later, which
# would turn one match into two rows. Not the competition either: 90minut calls
# it "VI liga" where futbolowo calls it "Klasa A", so it does not travel between
# sources. Within one season an ordered pair of teams meets exactly once.
# ponytail: a cup tie between the same pair in the same season would collide -
# add the competition tier to the key if that ever actually happens.
MatchKey = tuple[str, str, str]

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


def season_for(date: dt.date) -> str:
    """Calendar date -> the season label sources print, e.g. "2026/27".

    Polish football runs July to June. 90minut prints the label outright; the
    other sources do not, so they derive it here and land on the same MatchKey.
    """
    start = date.year if date.month >= 7 else date.year - 1
    return f"{start}/{(start + 1) % 100:02d}"


def normalize_player(name: str) -> str:
    """Player name reduced to a comparison key. Alias resolution proper ('M. Bak'
    vs 'Bak Mateusz') lands in merge.py in phase 3 - this is only the cheap part."""
    folded = name.casefold().translate(_STROKED_L)
    decomposed = unicodedata.normalize("NFKD", folded)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _NON_ALNUM.sub(" ", ascii_only).strip()


@dataclass(frozen=True, slots=True)
class MatchData:
    season: str  # "2026/27", as the source labels it
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
        return (self.season, normalize_team(self.home), normalize_team(self.away))


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

    url: str
    title: str
    published_at: dt.datetime | None
    text: str
    match: MatchKey | None = None
    """None when the report could not be tied to a match with confidence. It is
    still stored, so it can be matched by hand rather than silently dropped."""
