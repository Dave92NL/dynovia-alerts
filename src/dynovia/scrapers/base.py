"""Common interface for every source scraper.

The split that matters: fetch() touches the network, parse() never does. parse()
is handed a plain {key: html} dict, so the whole parser test suite runs offline on
the snapshots in tests/fixtures/ and development never hammers these small sites.

Adding a sixth source should mean one new file: subclass Scraper, implement
seed_pages() and parse(), register it in scrapers/__init__.py.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from dynovia.config import CACHE_DIR
from dynovia.models import (
    AppearanceData,
    CardData,
    GoalData,
    MatchData,
    MatchReport,
    TableRow,
)

REPO_URL = "https://github.com/Dave92NL/dynovia-alerts"

DEFAULT_HEADERS = {
    "User-Agent": f"dynovia-alerts/0.1 (private club notifier; +{REPO_URL})",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
}


class ScraperError(RuntimeError):
    """A source failed. The runner catches this per scraper so one dead site
    cannot take down the whole run."""


@dataclass(slots=True)
class ScrapeResult:
    """Everything one scrape of one source produced.

    A single shape with optional lists instead of five different return types:
    90minut brings matches and a table, futbolowo lineups and goals,
    laczynaspilka cards. merge.py then picks per field according to the trust
    table, without caring which scraper filled which list.
    """

    source: str
    fetched_at: dt.datetime
    matches: list[MatchData] = field(default_factory=list)
    lineups: list[AppearanceData] = field(default_factory=list)
    goals: list[GoalData] = field(default_factory=list)
    cards: list[CardData] = field(default_factory=list)
    table: list[TableRow] = field(default_factory=list)
    reports: list[MatchReport] = field(default_factory=list)


class Scraper(ABC):
    name: str = ""
    """Source identifier. Goes straight into the `source` column of every row."""

    primary: str = "matches"
    """The ScrapeResult list that must not come back empty. An empty league table
    before round 1 is legitimate; an empty fixture list is a parser failure."""

    encoding: str | None = None
    """Forced response encoding. 90minut serves ISO-8859-2 without declaring it."""

    min_interval: float = 1.0
    """Minimum seconds between two requests of this scraper. futbolowo needs 3."""

    headers: dict[str, str] = DEFAULT_HEADERS

    def __init__(self) -> None:
        self._last_request = 0.0

    # --- to implement in a subclass -------------------------------------------

    @abstractmethod
    def seed_pages(self) -> dict[str, str]:
        """{fixture key: url} - the pages this scraper always needs."""

    def follow_pages(self, pages: dict[str, str]) -> dict[str, str]:
        """Extra pages whose urls are only known after reading the seed pages,
        e.g. the league page linked from the club page."""
        return {}

    @abstractmethod
    def parse(self, pages: dict[str, str]) -> ScrapeResult:
        """Pure: {key: html} in, data out. Must not touch the network."""

    # --- provided -------------------------------------------------------------

    MAX_ROUNDS = 4
    """How many times follow_pages() may ask for more pages. 90minut needs three
    (season list -> season fixtures -> league table); the cap is only there to
    stop a buggy scraper from looping forever."""

    def fetch(self) -> ScrapeResult:
        pages = self.download(self.seed_pages())
        for _ in range(self.MAX_ROUNDS):
            missing = {
                key: url
                for key, url in self.follow_pages(pages).items()
                if key not in pages
            }
            if not missing:
                break
            pages |= self.download(missing)
        result = self.parse(pages)
        if not getattr(result, self.primary):
            raise ScraperError(
                f"{self.name}: parsed 0 {self.primary} - a failure, not 'no matches'"
            )
        return result

    def new_result(self, **lists) -> ScrapeResult:
        return ScrapeResult(
            source=self.name, fetched_at=dt.datetime.now(dt.UTC), **lists
        )

    def download(self, pages: dict[str, str]) -> dict[str, str]:
        """Sequential and throttled, never concurrent: these are small
        community-run sites and futbolowo blocks anything that looks like a bot."""
        return {key: self._get(url) for key, url in pages.items()}

    def _get(self, url: str) -> str:
        self._throttle()
        body, etag_file = _cache_paths(url)
        headers = dict(self.headers)
        if body.is_file() and etag_file.is_file():
            headers["If-None-Match"] = etag_file.read_text(encoding="utf-8")

        resp = self._request(url, headers)
        if resp.status_code == 304 and body.is_file():
            return body.read_text(encoding="utf-8")
        if resp.status_code >= 400:
            raise ScraperError(f"{self.name}: {url} returned HTTP {resp.status_code}")
        if self.encoding:
            resp.encoding = self.encoding

        text = resp.text
        body.parent.mkdir(parents=True, exist_ok=True)
        body.write_text(text, encoding="utf-8")
        etag = resp.headers.get("ETag")
        if etag:
            etag_file.write_text(etag, encoding="utf-8")
        elif etag_file.is_file():
            etag_file.unlink()
        return text

    def _request(self, url: str, headers: dict[str, str]) -> httpx.Response:
        delay = 1.0
        for attempt in range(3):
            try:
                resp = httpx.get(
                    url, headers=headers, timeout=20.0, follow_redirects=True
                )
                if resp.status_code < 500:
                    return resp
            except httpx.TransportError as exc:
                if attempt == 2:
                    raise ScraperError(f"{self.name}: {url} unreachable: {exc}") from exc
            time.sleep(delay)
            delay *= 2
        raise ScraperError(f"{self.name}: {url} kept returning 5xx")

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()


def _cache_paths(url: str) -> tuple[Path, Path]:
    stem = hashlib.sha1(url.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{stem}.html", CACHE_DIR / f"{stem}.etag"
