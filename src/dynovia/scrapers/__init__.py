"""Scraper registry. One dict entry per source, keyed by Scraper.name."""

from dynovia.scrapers.base import Scraper, ScraperError, ScrapeResult
from dynovia.scrapers.futbolowo import Futbolowo
from dynovia.scrapers.ninetyminut import NinetyMinut

SCRAPERS: dict[str, type[Scraper]] = {
    NinetyMinut.name: NinetyMinut,
    Futbolowo.name: Futbolowo,
}

__all__ = ["SCRAPERS", "Scraper", "ScraperError", "ScrapeResult"]
