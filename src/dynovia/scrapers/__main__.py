"""Print what a source's stored snapshot parses to - the checkpoint for every
scraper, offline.

    py -3.12 -m dynovia.scrapers 90minut
"""

from __future__ import annotations

import sys

from dynovia.scrapers import SCRAPERS
from dynovia.snapshot import load_fixtures


def main(argv: list[str]) -> None:
    if len(argv) != 1 or argv[0] not in SCRAPERS:
        sys.exit(f"usage: python -m dynovia.scrapers {{{'|'.join(SCRAPERS)}}}")

    scraper = SCRAPERS[argv[0]]()
    result = scraper.parse(load_fixtures(scraper.name))

    competitions = sorted({m.competition for m in result.matches})
    print(f"source: {result.source}   competitions: {', '.join(competitions) or '-'}")
    print(f"{len(result.matches)} matches\n")
    for m in result.matches:
        when = f"{m.date or '?':%Y-%m-%d}" if m.date else "?" * 10
        when += f" {m.time:%H:%M}" if m.time else "      "
        score = f"{m.home_score}-{m.away_score}" if m.status == "finished" else "-"
        print(
            f"{when}  k{str(m.round or '?'):<3} {m.home:>30} {score:^7} "
            f"{m.away:<30} [{m.status}]"
        )

    for label in ("lineups", "goals", "cards", "table", "reports"):
        rows = getattr(result, label)
        if rows:
            print(f"\n{label}: {len(rows)}")


if __name__ == "__main__":
    main(sys.argv[1:])
