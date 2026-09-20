"""Matching the spellings the sources actually use against a squad roster."""

from dynovia.models import normalize_player
from dynovia.players import load_roster, resolve

SQUAD = [
    "Tomasz Bielaszka",
    "Łukasz Kozioł",
    "Andrzej Goleś",
    "Filip Goleś",
    "Sylwester Paszko",
    "Michael Steve Londono Silva",
    "Grzegorz Szczepański",
]
REGISTRY = {normalize_player(name): name for name in SQUAD}


def test_a_full_name_matches_itself():
    assert resolve("Filip Goleś", REGISTRY) == ("Filip Goleś", [])


def test_a_bare_surname_resolves_when_only_one_player_has_it():
    assert resolve("Bielaszka", REGISTRY) == ("Tomasz Bielaszka", [])


def test_a_shared_surname_is_never_guessed():
    # The club has two players called Goleś and one of them scores. Choosing
    # either would misattribute goals and minutes for the whole season.
    player, candidates = resolve("Goleś", REGISTRY)
    assert player is None
    assert candidates == ["Andrzej Goleś", "Filip Goleś"]


def test_an_initial_disambiguates():
    assert resolve("F. Goleś", REGISTRY) == ("Filip Goleś", [])
    assert resolve("S. Paszko", REGISTRY) == ("Sylwester Paszko", [])


def test_a_partial_surname_matches_a_longer_one():
    # futbolowo writes "Londono", the protocol "Michael Steve Londono Silva".
    assert resolve("Londono", REGISTRY) == ("Michael Steve Londono Silva", [])


def test_diacritics_do_not_matter():
    assert resolve("Kozioł", REGISTRY) == resolve("Koziol", REGISTRY)
    assert resolve("Szczepanski", REGISTRY) == ("Grzegorz Szczepański", [])


def test_an_unknown_name_yields_no_candidates():
    # A new signing, or an opponent that leaked past a parser. Either way it is
    # a question, not a match.
    assert resolve("Jan Kłusek", REGISTRY) == (None, [])


def test_the_roster_file_ignores_blanks_and_comments(tmp_path):
    path = tmp_path / "kadra.txt"
    path.write_text(
        "# bramkarze\nTomasz Bielaszka\n\nŁukasz Kozioł  # obrońca\n", encoding="utf-8"
    )
    assert load_roster(path) == {
        "tomasz bielaszka": "Tomasz Bielaszka",
        "lukasz koziol": "Łukasz Kozioł",
    }


def test_a_missing_roster_file_is_empty_not_an_error():
    assert load_roster(__import__("pathlib").Path("nie-ma-takiego.txt")) == {}
