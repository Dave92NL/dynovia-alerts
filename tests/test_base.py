"""Offline checks for the two pieces phase 3 leans on: name keys and the
"empty result is an error" rule."""

import pytest

from dynovia.models import normalize_team
from dynovia.scrapers.base import Scraper, ScraperError


def test_team_name_variants_collapse_to_one_key():
    assert normalize_team("LKS Dynovia Dynów") == normalize_team("Dynovia Dynow")
    assert normalize_team("Dynovia Dynów") != normalize_team("Jawor Krzemienica")


def test_stroked_l_is_folded_too():
    assert normalize_team("Bratek Błażowa") == "bratek blazowa"


class _SilentSource(Scraper):
    """A source whose pages parse to nothing. Never goes online."""

    name = "test"

    def seed_pages(self):
        return {}

    def download(self, pages):
        return {}

    def parse(self, pages):
        return self.new_result()


def test_empty_primary_list_is_an_error_not_an_empty_schedule():
    with pytest.raises(ScraperError):
        _SilentSource().fetch()
