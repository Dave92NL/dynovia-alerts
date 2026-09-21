"""Combining what several sources say about the same match.

Two rules, both from the plan:

* Per field, the most trusted source that actually has a value wins. Not the
  newest, not the last one written - a fixed order, so the answer does not
  depend on which scraper happened to run last.
* A disagreement is never settled quietly. The winner is still used, because
  the app has to show something, but every dissenting value is recorded as a
  conflict and goes out as an alert.

"No value" is not a disagreement. futbolowo listing yesterday's match with an
empty score is a gap for a better source to fill, not a contradiction.
"""

from __future__ import annotations

from dataclasses import dataclass

from dynovia.models import MatchData

TRUST: dict[str, tuple[str, ...]] = {
    # laczynaspilka first everywhere: it is the PZPN record, so when it has
    # something to say it outranks every scraper. It only speaks when the owner
    # imports it by hand, which is why the others still matter.
    #
    # Below it, regiowyniki ahead of 90minut, against the plan's table: the two
    # disagreed about rounds 9 and 10 and the PZPN schedule gave 11.10 and
    # 18.10, which is regiowyniki both times.
    "date": ("laczynaspilka", "regiowyniki", "90minut", "futbolowo", "podkarpacielive"),
    "time": ("laczynaspilka", "regiowyniki", "90minut", "futbolowo", "podkarpacielive"),
    "score": ("laczynaspilka", "90minut", "regiowyniki", "podkarpacielive", "futbolowo"),
    # Not in the plan's table, because the plan did not expect the sources to
    # disagree about the league's name. 90minut prints "VI liga" (its generic
    # sixth-tier label) and futbolowo prints nothing, so regiowyniki leads here
    # with the name a human would use: "Klasa A".
    "competition": (
        "laczynaspilka",
        "regiowyniki",
        "90minut",
        "futbolowo",
        "podkarpacielive",
    ),
}

GOAL_TRUST = ("laczynaspilka", "podkarpacielive", "futbolowo")
"""Whose scorer list to believe, most trusted first.

The PZPN protocol leads when it has been imported. The plan put futbolowo
first; measured against that protocol it is the least reliable of the three,
having recorded Dynovia's second goal against Grom as an own goal where both
the protocol and podkarpacielive name Filip Goleś in the 42nd minute."""

SILENT = frozenset({"competition"})
"""Fields where the sources differ by convention rather than by mistake. The
league is "VI liga" on 90minut and "Klasa A" on regiowyniki for every single
match, forever; alerting on that would bury the disagreements that matter."""

_VALUE = {
    "date": lambda m: m.date,
    "time": lambda m: m.time,
    "competition": lambda m: m.competition or None,
    "score": lambda m: None if m.home_score is None else (m.home_score, m.away_score),
}


@dataclass(frozen=True, slots=True)
class Conflict:
    field: str
    source_a: str
    value_a: str
    source_b: str
    value_b: str

    def describe(self) -> str:
        if self.field == "zawodnik":
            fits = self.value_b or "nikt z kadry"
            return f"kto to jest \"{self.value_a}\" ({self.source_a})? Pasuje: {fits}"
        if self.source_a == self.source_b:
            # One source contradicting itself, which reads badly as "x A, x B".
            return f"{self.source_a} ({self.field}): {self.value_a} / {self.value_b}"
        return (
            f"{self.field}: {self.source_a} {self.value_a}, "
            f"{self.source_b} {self.value_b}"
        )


def _rank(source: str, field: str) -> int:
    order = TRUST.get(field, ())
    return order.index(source) if source in order else len(order)


def pick(field: str, views: dict[str, MatchData]) -> tuple[object, list[Conflict]]:
    """The winning value for one field, plus every source that disagreed."""
    known = {}
    for source, view in views.items():
        value = _VALUE[field](view)
        if value is not None:
            known[source] = value
    if not known:
        return None, []

    ranked = sorted(known, key=lambda source: (_rank(source, field), source))
    winner = ranked[0]
    value = known[winner]
    if field in SILENT:
        return value, []
    conflicts = [
        Conflict(field, winner, str(value), other, str(known[other]))
        for other in ranked[1:]
        if known[other] != value
    ]
    return value, conflicts


def merge(views: dict[str, MatchData]) -> tuple[MatchData, list[Conflict]]:
    """One match as several sources see it -> one match, plus disagreements."""
    if not views:
        raise ValueError("merge() needs at least one source view")

    conflicts: list[Conflict] = []
    values: dict[str, object] = {}
    for field in _VALUE:
        value, found = pick(field, views)
        values[field] = value
        conflicts += found

    score = values["score"] or (None, None)
    # Team names and season are the match key, identical across sources by
    # definition, so the most trusted view simply supplies the spelling.
    base = views[min(views, key=lambda source: (_rank(source, "date"), source))]
    merged = MatchData(
        season=base.season,
        date=values["date"],
        time=values["time"],
        competition=values["competition"] or "",
        home=base.home,
        away=base.away,
        round=next((v.round for v in views.values() if v.round is not None), None),
        home_score=score[0],
        away_score=score[1],
        status="finished" if score[0] is not None else _status(views),
        venue=next((v.venue for v in views.values() if v.venue), None),
        external_id=base.external_id,
    )
    return merged, conflicts


def _status(views: dict[str, MatchData]) -> str:
    statuses = {v.status for v in views.values()}
    return "postponed" if statuses == {"postponed"} else "scheduled"
