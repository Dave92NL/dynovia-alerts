"""Scraper registry. One dict entry per source, keyed by Scraper.name."""

from dynovia.scrapers.base import Scraper, ScraperError, ScrapeResult

SCRAPERS: dict[str, type[Scraper]] = {}

__all__ = ["SCRAPERS", "Scraper", "ScraperError", "ScrapeResult"]
