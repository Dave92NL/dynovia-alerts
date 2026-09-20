"""Store one source's pages as test fixtures, then work offline.

    py -3.12 -m dynovia.snapshot 90minut

It drives the scraper's own seed_pages()/follow_pages(), so the fixtures always
match what that scraper really downloads - they cannot drift apart.
"""

from __future__ import annotations

import sys

from dynovia.config import FIXTURES_DIR
from dynovia.scrapers import SCRAPERS


def load_fixtures(name: str) -> dict[str, str]:
    """Fixture directory -> the same {key: html} dict that download() returns."""
    directory = FIXTURES_DIR / name
    return {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted(directory.glob("*.html"))
    }


def main(argv: list[str]) -> None:
    if len(argv) != 1 or argv[0] not in SCRAPERS:
        sys.exit(f"usage: python -m dynovia.snapshot {{{'|'.join(SCRAPERS) or '...'}}}")

    scraper = SCRAPERS[argv[0]]()
    pages = scraper.download(scraper.seed_pages())
    pages |= scraper.download(scraper.follow_pages(pages))

    out = FIXTURES_DIR / scraper.name
    out.mkdir(parents=True, exist_ok=True)
    for key, html in pages.items():
        target = out / f"{key}.html"
        target.write_text(html, encoding="utf-8")
        print(f"{target}  ({len(html)} chars)")


if __name__ == "__main__":
    main(sys.argv[1:])
