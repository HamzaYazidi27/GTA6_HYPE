from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).parent))

from fakes import ARTICLE_TEXT, ARTICLE_URL, FakeTTS, ScriptedLLM  # noqa: E402

from gta_factory.app.config.settings import get_settings, reset_settings_cache  # noqa: E402
from gta_factory.app.database import db as dbmod  # noqa: E402
from gta_factory.providers.llm import register_llm  # noqa: E402
from gta_factory.providers.tts import register_tts  # noqa: E402

register_llm("scripted", ScriptedLLM)
register_tts("fake", FakeTTS)
from gta_factory.modules.asset_management import rights as _rights  # noqa: E402

_rights.VOICE_LICENSES["fake"] = ("Synthetic test tone", True, "approved")

RENDER = os.environ.get("GTA_FACTORY_RENDER_TESTS") == "1"


@pytest.fixture(scope="session")
def audio_assets(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The original music beds and SFX: the repo's own if `setup` already ran, else synthesised once per session."""
    from gta_factory.modules.asset_management.audio_library import build_library, library_ready

    if not library_ready(REPO / "assets"):
        return REPO / "assets"
    assets = tmp_path_factory.mktemp("audio_assets")
    build_library(assets, log=lambda _m: None)
    return assets


@pytest.fixture()
def project(tmp_path: Path, audio_assets: Path):
    """A throw-away project root wired to the offline fakes."""
    root = tmp_path / "proj"
    root.mkdir()
    for sub in ("music", "sound_effects"):
        src = audio_assets / sub
        dst = root / "assets" / sub
        dst.mkdir(parents=True)
        for f in src.glob("*.wav"):
            shutil.copyfile(f, dst / f.name)
    (root / "assets" / "licensed_visuals").mkdir(parents=True)
    (root / "knowledge").mkdir()
    # rendering needs the real templates and node_modules
    (root / "templates").symlink_to(REPO / "templates")
    (root / "node_modules").symlink_to(REPO / "node_modules")
    cfg = {
        "providers": {"llm": {"default": "scripted"}, "tts": {"default": "fake"}, "search": {"providers": []}, "stock": {"pexels": {"enabled": False}, "pixabay": {"enabled": False}}},
        "video": {"render_concurrency": 4},
    }
    (root / "config.yaml").write_text(yaml.safe_dump(cfg))
    article = root / "article.txt"
    article.write_text(ARTICLE_TEXT)
    os.environ["GTA_FACTORY_DB"] = str(root / "jobs" / "factory.sqlite3")
    reset_settings_cache()
    dbmod._DB_CACHE.clear()
    settings = get_settings(root=root, config_file=root / "config.yaml")
    yield {"root": root, "settings": settings, "article": article, "url": ARTICLE_URL}
    os.environ.pop("GTA_FACTORY_DB", None)
    reset_settings_cache()
    dbmod._DB_CACHE.clear()
    # test jobs stage their media into the real bundle (templates/ is a symlink): clean those folders only
    staged = REPO / "templates" / ".bundle" / "public" / "job_media"
    for job_dir in (root / "jobs").glob("*"):
        shutil.rmtree(staged / job_dir.name, ignore_errors=True)


def job_options(project: dict, **extra) -> dict:
    base = {"text_files": {project["url"]: str(project["article"])}, "no_search": True, "whisper": False, "today": "2026-11-16", "target_seconds": 40}
    base.update(extra)
    return base
