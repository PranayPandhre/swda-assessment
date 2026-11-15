import shutil

import pytest
from collusion_sim.config import ROOT, load_settings
from collusion_sim.data import SourceLibrary
from collusion_sim.safeguards import Safeguards
from p2_helpers import FIXED


@pytest.fixture
def settings():
    return load_settings()


@pytest.fixture
def sg(settings):
    return Safeguards(settings.safeguards)


@pytest.fixture
def lib(settings, sg):
    return SourceLibrary(settings.path("data/annex_a_mo_library.json"), settings.path("data/annex_b_social_media.json"), sg)


@pytest.fixture
def clock():
    return lambda: FIXED


@pytest.fixture
def sandbox(tmp_path):
    """A writable copy of the project (config, data, scenarios, evals) so tests can mutate it safely."""
    dst = tmp_path / "proj"
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns("outputs", "__pycache__", "*.egg-info", "src", "tests"))
    return load_settings(dst)
