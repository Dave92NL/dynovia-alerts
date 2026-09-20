"""Store one source's pages as test fixtures, then work offline.

    py -3.12 -m dynovia.snapshot 90minut

It drives the scraper's own seed_pages()/follow_pages(), so the fixtures always
match what that scraper really downloads - they cannot drift apart.
"""

from __future__ import annotations

import re
import sys

from dynovia.config import FIXTURES_DIR
from dynovia.scrapers import SCRAPERS


_BASE64_BLOB = re.compile(r"data:[a-z/+.-]+;base64,[A-Za-z0-9+/=]+")


def strip_inline_images(html: str) -> str:
    """Blank out inline base64 payloads before storing a fixture.

    A futbolowo article is 2 MB of which 95% is one base64 image. No parser
    looks at it and the repo is public, so the snapshot keeps the markup and
    throws the payload away.
    """
    return _BASE64_BLOB.sub("data:,", html)


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
    for _ in range(scraper.MAX_ROUNDS):
        missing = {
            key: url
            for key, url in scraper.follow_pages(pages).items()
            if key not in pages
        }
        if not missing:
            break
        pages |= scraper.download(missing)

    out = FIXTURES_DIR / scraper.name
    out.mkdir(parents=True, exist_ok=True)
    for key, html in pages.items():
        target = out / f"{key}.html"
        stored = strip_inline_images(html)
        target.write_text(stored, encoding="utf-8")
        note = f", {len(html) - len(stored)} chars of inline images dropped" if len(stored) != len(html) else ""
        print(f"{target}  ({len(stored)} chars{note})")


if __name__ == "__main__":
    main(sys.argv[1:])
