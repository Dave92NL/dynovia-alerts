"""Turning the many spellings of a name into one player.

The sources are wildly inconsistent. futbolowo writes lineups as bare surnames
("Bielaszka, Kozioł"), goal lines sometimes as initials ("S. Paszko") and
sometimes in full. podkarpacielive and the PZPN protocols write full names.

So a squad roster is the registry, and every other spelling is matched against
it. The rule that matters: an ambiguous name is never guessed. The club has an
Andrzej Goleś and a Filip Goleś, so a lineup reading "Goleś" has two possible
answers, and picking one would quietly misattribute goals and minutes for the
rest of the season. Unresolved names are raised as questions instead.
"""

from __future__ import annotations

from pathlib import Path

from dynovia.models import normalize_player

ROSTER_FILE = "kadra.txt"


def load_roster(path: Path) -> dict[str, str]:
    """A registry: {normalized spelling: canonical name}. One player per line,
    blank lines and #-comments ignored.
    A line may also pin a spelling that would otherwise be ambiguous:

        Filip Goleś = Goleś, F. Goleś

    which is how a confirmed answer stops the same question coming back.
    """
    if not path.is_file():
        return {}
    registry = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.split("#")[0].strip()
        if not entry:
            continue
        canonical, _, aliases = entry.partition("=")
        canonical = canonical.strip()
        registry[normalize_player(canonical)] = canonical
        for alias in aliases.split(","):
            if alias.strip():
                registry[normalize_player(alias)] = canonical
    return registry


def _covers(shorter: list[str], longer: list[str]) -> bool:
    used: set[int] = set()
    for word in shorter:
        match = next(
            (
                index
                for index, candidate in enumerate(longer)
                if index not in used
                and (candidate == word or (len(word) == 1 and candidate.startswith(word)))
            ),
            None,
        )
        if match is None:
            return False
        used.add(match)
    return True


def _fits(written: list[str], full: list[str]) -> bool:
    """Whether two spellings can be the same person.

    Matching runs in whichever direction is shorter, because neither side is
    reliably the fuller one. The roster says "Michael Londono Silva" while the
    PZPN protocol says "Michael Steve Londono Silva"; futbolowo just says
    "Londono". A single letter matches a word starting with it, so "S. Paszko"
    fits "Sylwester Paszko".
    """
    if len(written) <= len(full):
        return _covers(written, full)
    return _covers(full, written)


def resolve(written: str, registry: dict[str, str]) -> tuple[str | None, list[str]]:
    """(player, []) when certain, (None, candidates) when not.

    Candidates being empty means nobody in the roster fits at all - a new
    signing, or an opponent that leaked through a parser.
    """
    key = normalize_player(written)
    if not key:
        return None, []
    if key in registry:
        return registry[key], []

    words = key.split()
    # By canonical name: a player with a pinned alias appears under several
    # keys and would otherwise look like several candidates.
    candidates = {
        name for normalized, name in registry.items() if _fits(words, normalized.split())
    }
    if len(candidates) == 1:
        return candidates.pop(), []
    return None, sorted(candidates)
