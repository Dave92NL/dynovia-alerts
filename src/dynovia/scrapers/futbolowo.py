"""futbolowo.pl - the club's own site, and the only source for lineups.

Three findings from the reconnaissance that shape this file:

* The RSS feed at /feed exists but is useless: 42 MB and no ETag or
  Last-Modified, so every run would redownload all of it. HTML it is.
* Publication dates are unreliable. A report of a Sunday match carries the
  previous Wednesday, so reports are tied to matches by the teams named in
  them, never by date.
* Reports come in two shapes. Some open with a header line
  ("Dynovia 1:1 (1:1) KS Dąbrówki" / "Bramka: Ruslan Kovtok"), some describe
  the goals in prose. Scorers are only read from the header line; the prose
  ones are stored verbatim for phase 5 to deal with.
"""

from __future__ import annotations

import datetime as dt
import re

from selectolax.parser import HTMLParser

from dynovia.models import (
    AppearanceData,
    GoalData,
    MatchData,
    MatchKey,
    MatchReport,
    normalize_team,
    season_for,
)
from dynovia.scrapers.base import ScrapeResult, Scraper

BASE = "https://dynovia-dynow.futbolowo.pl"

MAX_ARTICLES_PER_RUN = 3
"""Each article is ~2 MB before image stripping and reports appear about once a
week, so there is never a backlog worth more than this in a single run."""

# The club's own team cell carries the site's tagline. Deterministic, so it is
# stripped here rather than turned into an alias.
_SITE_SUFFIX = re.compile(r"\s*[-–—]\s*oficjalna strona klubowa\s*$", re.I)

_SCORE = re.compile(r"(\d+)\s*:\s*(\d+)")
_HEADER = re.compile(
    r"^\s*(?P<home>.+?)\s+(?P<hs>\d+)\s*:\s*(?P<as>\d+)\s*(?:\([^)]*\))?\s*(?P<away>.+?)\s*$"
)
_GOAL_LINE = re.compile(r"Bramk[ai][^:]*:\s*(?P<names>[^\n]+)")
_LINEUP = re.compile(
    # Anchored to the start of a line: the goal line reads "Bramki Dynovia:"
    # and would otherwise be picked up as a lineup.
    r"^Dynovia\s*:\s*(?P<starters>[^\n]+?)"
    r"(?:\s*Na\s+zmiany\s*:\s*(?P<subs>[^\n]+))?$",
    re.M,
)
_WHITESPACE = re.compile(r"\s+")
_BLOCK_END = re.compile(r"(?i)</(?:p|div|li|h[1-6])>|<br\s*/?>")
_INLINE_SPACE = re.compile("[ \t\xa0]+")

# One to three capitalised words, an initial allowed for the first: the club
# writes both "Sylwester Paszko" and "S. Paszko".
_NAME = re.compile(
    r"^[A-ZĄĆĘŁŃÓŚŹŻ](?:\.|[\w'’-]+)(?: [A-ZĄĆĘŁŃÓŚŹŻ](?:\.|[\w'’-]+)){0,2}$"
)
# Split on commas, and on a full stop before a capital - but never right after
# one, or "S. Paszko" would be torn in half. The club writes "gol sam. Duchniak"
# where it means a comma.
_NAME_SEP = re.compile(r"[,;]|(?<![A-ZĄĆĘŁŃÓŚŹŻ])\.\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ])")


def clean_team(name: str) -> str:
    return _SITE_SUFFIX.sub("", name).strip()


def _text(node) -> str:
    """Node text on one line. futbolowo wraps names in <strong> without
    surrounding spaces, so joining without one yields 'otworzyłArkadiusz'."""
    return _WHITESPACE.sub(" ", node.text(separator=" ", strip=True)).strip()


def _block_text(node) -> str:
    """Report text with paragraph breaks kept and inline breaks discarded.

    The header, the goal line and the lineup are separate <p> blocks, so the
    parser needs those newlines to tell them apart. Splitting on inline tags
    instead would tear every sentence apart, because the names inside the prose
    sit in their own <strong>.
    """
    text = HTMLParser(_BLOCK_END.sub("\n", node.html)).text(separator=" ")
    lines = (_INLINE_SPACE.sub(" ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def parse_fixtures(html: str) -> list[MatchData]:
    """The league's fixture table. Only Dynovia's own rows are kept; the page
    also lists every other pairing and the byes, which are not matches."""
    matches = []
    for row in HTMLParser(html).css("tr.gameRow"):
        cells = {
            name: row.css_first(f"td.{name}")
            for name in ("gameDate", "teamHome", "gameScore", "teamAway")
        }
        dates = [_text(c) for c in row.css("td.gameDate")]
        home = clean_team(_text(cells["teamHome"])) if cells["teamHome"] else ""
        away = clean_team(_text(cells["teamAway"])) if cells["teamAway"] else ""
        if "dynovia" not in f"{normalize_team(home)} {normalize_team(away)}":
            continue

        date = _parse_date(dates[0] if dates else "")
        if date is None:
            continue
        time = _parse_time(dates[1] if len(dates) > 1 else "")
        score = _SCORE.search(_text(cells["gameScore"]) if cells["gameScore"] else "")
        matches.append(
            MatchData(
                season=season_for(date),
                date=date,
                time=time,
                # futbolowo never names the league anywhere on the page.
                # Left blank on purpose so the schedule source keeps the name.
                competition="",
                home=home,
                away=away,
                home_score=int(score.group(1)) if score else None,
                away_score=int(score.group(2)) if score else None,
                status="finished" if score else "scheduled",
            )
        )
    return matches


def _parse_date(text: str) -> dt.date | None:
    match = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", text)
    if not match:
        return None
    day, month, year = match.groups()
    return dt.date(int(year), int(month), int(day))


def _parse_time(text: str) -> dt.time | None:
    match = re.search(r"(\d{1,2}):(\d{2})", text)
    return dt.time(int(match.group(1)), int(match.group(2))) if match else None


def parse_article_list(html: str) -> list[tuple[str, str, dt.datetime | None]]:
    """[(url, title, published_at)] newest first, as the listing orders them."""
    out = []
    for wrapper in HTMLParser(html).css(".article-wrapper"):
        link = wrapper.css_first("a[href]")
        title = wrapper.css_first(".news-title")
        stamp = wrapper.css_first("time[datetime]")
        if not link or not title:
            continue
        published = None
        if stamp:
            try:
                published = dt.datetime.fromisoformat(stamp.attributes["datetime"])
            except ValueError:
                published = None
        out.append((BASE + link.attributes["href"], _text(title), published))
    return out


def article_slug(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def parse_report(
    html: str, url: str, title: str, published: dt.datetime | None, known: list[MatchData]
) -> tuple[MatchReport, list[AppearanceData], list[GoalData]]:
    """One article. Returns the stored report plus whatever it yields.

    An article that is not a match report simply produces no lineup and no
    goals, and is still recorded so it is never downloaded twice.
    """
    node = HTMLParser(html).css_first("section.post-content")
    text = _block_text(node) if node else ""
    match_key = _identify(text, title, known)
    report = MatchReport(
        url=url, title=title, published_at=published, text=text, match=match_key
    )
    if match_key is None:
        return report, [], []

    lineup = _parse_lineup(text, match_key)
    goals = _parse_goals(text, match_key)
    return report, lineup, goals


def _identify(text: str, title: str, known: list[MatchData]) -> MatchKey | None:
    """Tie a report to a match by the teams it names, never by its date.

    First choice is the header line, which prints both teams unbent. Otherwise
    the opponent is looked up by word stem, because Polish inflects the name
    ("w Markowej", "z Gromem"). Ambiguity yields None rather than a guess - a
    report filed against the wrong match would poison the season's stats.
    """
    header = _HEADER.match(text.split("\n")[0].strip())
    if header and max(len(header.group("home")), len(header.group("away"))) <= 45:
        home, away = normalize_team(header.group("home")), normalize_team(
            header.group("away")
        )
        for match in known:
            if (normalize_team(match.home), normalize_team(match.away)) == (home, away):
                return match.key

    haystack = normalize_team(f"{title} {text[:600]}".replace("\n", " "))
    hits = {
        match.key
        for match in known
        if match.status == "finished" and _mentions_opponent(match, haystack)
    }
    return hits.pop() if len(hits) == 1 else None


def _mentions_opponent(match: MatchData, haystack: str) -> bool:
    opponent = match.away if "dynovia" in normalize_team(match.home) else match.home
    stems = [word[:6] for word in normalize_team(opponent).split() if len(word) >= 5]
    return any(
        any(token.startswith(stem) for token in haystack.split()) for stem in stems
    )


def _names(blob: str, *, stop_at_junk: bool) -> list[str]:
    """Split a name list, keeping only the entries that look like names.

    stop_at_junk is the whole difference between the two callers. A lineup is a
    contiguous run that ends where the prose begins, with nothing in the markup
    to mark the boundary, so the first non-name ends it - otherwise scorers
    named later in the report get recorded as substitutes. A goal line is not
    contiguous: "Paszko, Kovtok, gol sam. Duchniak" has an own goal in the
    middle, so there the junk is stepped over instead.
    """
    out = []
    for raw in _NAME_SEP.split(blob):
        name = _INLINE_SPACE.sub(" ", re.sub(r"\bkpt\b", "", raw, flags=re.I)).strip(" .;")
        if not name:
            continue
        if not _NAME.match(name) or normalize_team(name) == "dynovia":
            if stop_at_junk:
                break
            continue  # an own goal or a note like "(karny)" is not our player
        out.append(name)
    return out


def _parse_lineup(text: str, match: MatchKey) -> list[AppearanceData]:
    found = _LINEUP.search(text)
    if not found:
        return []
    starters = _names(found.group("starters"), stop_at_junk=True)
    subs = _names(found.group("subs") or "", stop_at_junk=True)
    return [AppearanceData(match=match, player=n, started=True) for n in starters] + [
        AppearanceData(match=match, player=n, started=False) for n in subs
    ]


def _parse_goals(text: str, match: MatchKey) -> list[GoalData]:
    """Only the explicit "Bramka:" line. Reports that narrate the goals in prose
    are left to phase 5 - guessing scorers out of prose here would be worse than
    having none."""
    found = _GOAL_LINE.search(text)
    if not found:
        return []
    names = _names(found.group("names"), stop_at_junk=False)
    return [GoalData(match=match, player=name) for name in names]


class Futbolowo(Scraper):
    name = "futbolowo"
    min_interval = 3.0  # the site blocks anything faster
    headers = {
        # A full browser set, not the project's own agent: futbolowo rejects
        # requests that do not look like a browser.
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
    }

    def seed_pages(self) -> dict[str, str]:
        return {"terminarz": f"{BASE}/terminarz", "news": f"{BASE}/news"}

    def follow_pages(self, pages: dict[str, str]) -> dict[str, str]:
        unseen = [
            (url, article_slug(url))
            for url, _, _ in parse_article_list(pages["news"])
            if url not in self.seen
        ]
        return {
            f"article_{slug}": url
            for url, slug in unseen[:MAX_ARTICLES_PER_RUN]
        }

    def parse(self, pages: dict[str, str]) -> ScrapeResult:
        matches = parse_fixtures(pages["terminarz"])
        listing = {
            article_slug(url): (url, title, published)
            for url, title, published in parse_article_list(pages["news"])
        }

        reports, lineups, goals = [], [], []
        for key, html in pages.items():
            if not key.startswith("article_"):
                continue
            entry = listing.get(key.removeprefix("article_"))
            if entry is None:
                continue
            report, appearances, scored = parse_report(html, *entry, matches)
            reports.append(report)
            lineups += appearances
            goals += scored

        return self.new_result(
            matches=matches, lineups=lineups, goals=goals, reports=reports
        )
