"""podkarpacielive.pl - results, and the only source with minutes for goals.

The team page lists finished matches from Dynovia's own point of view: the
opponent and a score with Dynovia's goals first, whoever was at home. Which way
round it really was comes from the match url slug ("markovia-markowa-vs-
dynovia-dynow"), so the score is flipped back here rather than stored skewed.

Match pages carry each goal with its minute and a link to the scorer's profile,
which is a stable per-player id. No other source has either.
"""

from __future__ import annotations

import datetime as dt
import re

from selectolax.parser import HTMLParser

from dynovia.models import CLUB, GoalData, MatchData, MatchKey, season_for
from dynovia.scrapers.base import ScrapeResult, Scraper

BASE = "https://www.podkarpacielive.pl"
TEAM_URL = f"{BASE}/statystyki/93,dynovia-dynow"

RECENT = dt.timedelta(days=4)
"""How far back to still open a match page. Goals trickle in for a day or two
after the whistle; past that the page is settled and worth 350 KB to nobody."""
MAX_MATCH_PAGES = 2

_MATCH_LINK = re.compile(r"/mecz/(?P<id>\d+),(?P<slug>[a-z0-9-]+)")
_SCORE = re.compile(r"(\d+)\s*[-:]\s*(\d+)")
_MINUTE = re.compile(r"(\d+)")


def _dynovia_at_home(slug: str) -> bool:
    host = slug.split("-vs-")[0]
    return "dynovia" in host


def parse_results(html: str, season: str) -> list[MatchData]:
    """Finished matches of one season, as the team page's recent-form list has
    them. Upcoming fixtures are left alone - three other sources cover the
    schedule, and this one is here for results and scorers."""
    # The page renders the same recent-form list in three places. These are
    # identical repeats, not a disagreement about who was at home, so they are
    # dropped here rather than reported as conflicts.
    matches: dict[str, MatchData] = {}
    for item in HTMLParser(html).css(".team-streak-item__row"):
        link = item.css_first("a[href]")
        date_cell = item.css_first(".team-streak-item__date")
        opponent = item.css_first(".team-streak-item__opp")
        score_cell = item.css_first(".team-streak-item__score")
        if not (link and date_cell and opponent and score_cell):
            continue
        found = _MATCH_LINK.search(link.attributes.get("href") or "")
        score = _SCORE.search(score_cell.text(strip=True))
        date = _parse_date(date_cell.text(strip=True))
        if not (found and score and date) or season_for(date) != season:
            continue

        ours, theirs = int(score.group(1)), int(score.group(2))
        at_home = _dynovia_at_home(found.group("slug"))
        home, away = (CLUB, opponent.text(strip=True))
        if not at_home:
            home, away = away, home
            ours, theirs = theirs, ours
        matches.setdefault(
            found.group("id"),
            MatchData(
                season=season,
                date=date,
                time=None,  # the list shows none; other sources have it
                competition="Klasa A",
                home=home,
                away=away,
                home_score=ours,
                away_score=theirs,
                status="finished",
                external_id=found.group("id"),
            ),
        )
    return list(matches.values())


def _parse_date(text: str) -> dt.date | None:
    found = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", text)
    if not found:
        return None
    day, month, year = found.groups()
    return dt.date(int(year), int(month), int(day))


def match_links(html: str) -> dict[str, str]:
    """{match id: slug} for every match the team page links to."""
    links = {}
    for link in HTMLParser(html).css("a[href]"):
        found = _MATCH_LINK.search(link.attributes.get("href") or "")
        if found:
            links[found.group("id")] = found.group("slug")
    return links


def parse_goals(html: str, match: MatchKey, *, dynovia_at_home: bool) -> list[GoalData]:
    """Dynovia's goals only. Each event line is marked host or guest, and the
    opponent's goals are somebody else's statistics."""
    goals = []
    for line in HTMLParser(html).css("div.match_line"):
        classes = line.attributes.get("class") or ""
        if ("match_line_host" in classes) != dynovia_at_home:
            continue
        bar = line.css_first(".match_event_bar--goal")
        if bar is None:
            continue
        player = bar.css_first(".match_event_player")
        minute_cell = bar.css_first(".match_event_minute")
        if player is None:
            continue
        minute = _MINUTE.search(minute_cell.text(strip=True) if minute_cell else "")
        goals.append(
            GoalData(
                match=match,
                player=player.text(strip=True),
                minute=int(minute.group(1)) if minute else None,
            )
        )
    return goals


class Podkarpacielive(Scraper):
    name = "podkarpacielive"

    def seed_pages(self) -> dict[str, str]:
        return {"team": TEAM_URL}

    def follow_pages(self, pages: dict[str, str]) -> dict[str, str]:
        season = season_for(dt.date.today())
        cutoff = dt.date.today() - RECENT
        links = match_links(pages["team"])
        recent = [
            match
            for match in parse_results(pages["team"], season)
            if match.date and match.date >= cutoff and match.external_id in links
        ]
        recent.sort(key=lambda match: match.date, reverse=True)
        return {
            f"match_{match.external_id}": (
                f"{BASE}/mecz/{match.external_id},{links[match.external_id]}"
            )
            for match in recent[:MAX_MATCH_PAGES]
        }

    def parse(self, pages: dict[str, str]) -> ScrapeResult:
        season = season_for(dt.date.today())
        matches = parse_results(pages["team"], season)
        links = match_links(pages["team"])
        by_id = {match.external_id: match for match in matches}

        goals = []
        for key, html in pages.items():
            if not key.startswith("match_"):
                continue
            match = by_id.get(key.removeprefix("match_"))
            if match is None:
                continue
            goals += parse_goals(
                html,
                match.key,
                dynovia_at_home=_dynovia_at_home(links[match.external_id]),
            )
        return self.new_result(matches=matches, goals=goals)
