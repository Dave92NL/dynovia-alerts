"""90minut.pl - fixtures and results. The foundation source (phase 1).

Plain HTML, no API, served as ISO-8859-2 without saying so. Low-league match rows
carry no per-match page, so this source gives schedule and score only - scorers
and lineups come from futbolowo later.
"""

from __future__ import annotations

import datetime as dt
import re

from selectolax.parser import HTMLParser

from dynovia.models import MatchData, MatchStatus, normalize_team
from dynovia.scrapers.base import ScrapeResult, Scraper, ScraperError

CLUB_ID = 65  # Dynovia Dynow, stable across seasons
BASE = "http://www.90minut.pl"

_ID_SEZON = re.compile(r"id_sezon=(\d+)")
_WHEN = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{1,2}):(\d{2}))?")
_SCORE = re.compile(r"(\d+)\s*-\s*(\d+)")
_COMPETITION = re.compile(r"^(?P<competition>.+?)(?:,\s*Kolejka\s*(?P<round>\d+))?$")


def latest_season(html: str) -> tuple[int, str]:
    """Newest (id_sezon, label) from the club's season dropdown.

    Detected, never computed: the plan's "+2 every season" rule is a hardcode in
    disguise that breaks the day 90minut renumbers. Asking without id_sezon gets
    you season 1972, so this id is required for every later request.
    """
    options = HTMLParser(html).css("select[name=urljump1] option")
    seasons = [
        (int(match.group(1)), option.text(strip=True))
        for option in options
        if (match := _ID_SEZON.search(option.attributes.get("value") or ""))
    ]
    if not seasons:
        raise ScraperError("90minut: no season dropdown on the club page")
    return max(seasons)


def _parse_when(text: str) -> tuple[dt.date | None, dt.time | None]:
    """'2026-08-16 17:00' -> (date, time). Rounds far ahead have no date at all
    and late-scheduled ones no kickoff time, so both halves are optional."""
    match = _WHEN.search(text)
    if not match:
        return None, None
    year, month, day, hour, minute = match.groups()
    kickoff = dt.time(int(hour), int(minute)) if hour else None
    return dt.date(int(year), int(month), int(day)), kickoff


def _parse_competition(text: str) -> tuple[str, int | None]:
    """'VI liga, Kolejka 1' -> ('VI liga', 1). Cup ties carry no round."""
    match = _COMPETITION.match(text.strip())
    if not match:
        return text.strip(), None
    round_no = match.group("round")
    return match.group("competition").strip(), int(round_no) if round_no else None


def _parse_score(text: str) -> tuple[int | None, int | None, MatchStatus]:
    """A digit pair means played - searched rather than matched so that a
    walkover ('3-0 wo.') still reads as a result. '-' means not played yet;
    anything else is some irregularity, bucketed as postponed."""
    match = _SCORE.search(text)
    if match:
        return int(match.group(1)), int(match.group(2)), "finished"
    return None, None, "scheduled" if text.strip() in {"-", ""} else "postponed"


def _is_our_match(home: str, away: str) -> bool:
    """Guards against the header row and any other five-column table."""
    return any("dynovia" in normalize_team(team) for team in (home, away))


def parse_matches(html: str) -> list[MatchData]:
    matches = []
    for row in HTMLParser(html).css("tr"):
        cells = [cell.text(strip=True) for cell in row.css("td")]
        if len(cells) != 5:
            continue
        when, competition_text, home, score, away = cells
        if not _is_our_match(home, away):
            continue
        date, kickoff = _parse_when(when)
        competition, round_no = _parse_competition(competition_text)
        home_score, away_score, status = _parse_score(score)
        matches.append(
            MatchData(
                date=date,
                time=kickoff,
                competition=competition,
                home=home,
                away=away,
                round=round_no,
                home_score=home_score,
                away_score=away_score,
                status=status,
            )
        )
    return matches


class NinetyMinut(Scraper):
    name = "90minut"
    encoding = "iso-8859-2"  # served without a charset declaration

    def seed_pages(self) -> dict[str, str]:
        return {"seasons": f"{BASE}/mecze_druzyna.php?id={CLUB_ID}"}

    def follow_pages(self, pages: dict[str, str]) -> dict[str, str]:
        season_id, _ = latest_season(pages["seasons"])
        return {
            "matches": f"{BASE}/mecze_druzyna.php?id={CLUB_ID}&id_sezon={season_id}"
        }

    def parse(self, pages: dict[str, str]) -> ScrapeResult:
        return self.new_result(matches=parse_matches(pages["matches"]))
