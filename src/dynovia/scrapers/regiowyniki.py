"""regiowyniki.pl - a second opinion on results, and the only source that names
the competition the way a human would.

90minut calls this league "VI liga" (its generic name for the sixth tier) and
futbolowo does not name it at all. regiowyniki calls it "Klasa A", which is what
the competition column ends up holding.

The catch: the page prints no year anywhere. Dates read "16sie/0817:00" - day,
month name, month number, kickoff, and nothing else - so the year is derived
from the season, which is why parse_matches takes it as an argument.
"""

from __future__ import annotations

import datetime as dt
import re

from selectolax.parser import HTMLParser

from dynovia.models import MatchData, season_for
from dynovia.scrapers.base import ScrapeResult, Scraper

BASE = "https://regiowyniki.pl"
TEAM_URL = f"{BASE}/druzyna/Pilka_Nozna/Podkarpackie/Dynovia_Dynow/"

# "16sie/0817:00" -> 16 August, 17:00. Unscheduled rows read "--/-?:?" and have
# no leading digits, so they simply do not match.
_WHEN = re.compile(
    r"(?P<day>\d{1,2})\D*?/(?P<month>\d{2})(?:(?P<hour>\d{1,2}):(?P<minute>\d{2}))?"
)
_MATCH_LINK = re.compile(r"/mecz/(?P<id>\d+)/[^/]+/[^/]+/(?P<competition>[^/]+)/")


def _competition(tree: HTMLParser) -> str:
    """League name, taken from any played match's url. One page, one league, so
    it is read once and applied to every row - unplayed rows carry no link."""
    for link in tree.css("a[href]"):
        found = _MATCH_LINK.search(link.attributes.get("href") or "")
        if found:
            return found.group("competition").replace("_", " ").strip()
    return ""


def _season_year(season: str, month: int) -> int:
    """Polish seasons straddle the new year: "2026/27" means Aug 2026, Mar 2027."""
    start = int(season.split("/")[0])
    return start if month >= 7 else start + 1


def _score(row) -> tuple[int | None, int | None]:
    digits = [cell.text(strip=True) for cell in row.css("div.goals")]
    if len(digits) != 2 or not all(d.isdigit() for d in digits):
        return None, None
    return int(digits[0]), int(digits[1])


def _external_id(row) -> str | None:
    for link in row.css("a[href]"):
        found = _MATCH_LINK.search(link.attributes.get("href") or "")
        if found:
            return found.group("id")
    return None


def parse_matches(html: str, season: str) -> list[MatchData]:
    tree = HTMLParser(html)
    competition = _competition(tree)
    matches = []
    for row in tree.css("div.row"):
        # The outer wrapper is a div.row too and holds every match at once, so
        # rows are identified by having exactly one date and exactly two teams.
        teams = row.css("div.team")
        date_cell = row.css_first("div.date")
        if date_cell is None or len(teams) != 2:
            continue

        home, away = (team.text(strip=True) for team in teams)
        when = _WHEN.search(date_cell.text(strip=True))
        date = time = None
        if when:
            month = int(when.group("month"))
            date = dt.date(_season_year(season, month), month, int(when.group("day")))
            if when.group("hour"):
                time = dt.time(int(when.group("hour")), int(when.group("minute")))

        home_score, away_score = _score(row)
        matches.append(
            MatchData(
                season=season,
                date=date,
                time=time,
                competition=competition,
                home=home,
                away=away,
                home_score=home_score,
                away_score=away_score,
                status="finished" if home_score is not None else "scheduled",
                external_id=_external_id(row),
            )
        )
    return matches


class Regiowyniki(Scraper):
    name = "regiowyniki"

    def seed_pages(self) -> dict[str, str]:
        return {"team": TEAM_URL}

    def parse(self, pages: dict[str, str]) -> ScrapeResult:
        # The page always shows the running season and never prints its year,
        # so today's date is what pins the fixtures to a calendar.
        return self.new_result(
            matches=parse_matches(pages["team"], season_for(dt.date.today()))
        )
