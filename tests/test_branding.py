"""The channel logo: cut to its circle, exported for YouTube Studio, staged as the watermark of every video."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from gta_factory.app.core.errors import ConfigError
from gta_factory.modules.branding import install_logo, round_logo, stage_brand
from gta_factory.modules.video_editing.render import bundle_dir


def _logo_on_black(path: Path, size: int = 600) -> Path:
    im = Image.new("RGB", (size, size), (0, 0, 0))
    ImageDraw.Draw(im).ellipse((0, 0, size - 1, size - 1), fill=(240, 60, 180))
    im.save(path)
    return path


def test_round_logo_cuts_the_circle_out_of_a_black_square(tmp_path: Path) -> None:
    logo = round_logo(Image.open(_logo_on_black(tmp_path / "logo.png")))
    assert logo.mode == "RGBA" and logo.size[0] == logo.size[1]
    w = logo.size[0]
    assert logo.getpixel((0, 0))[3] == 0 and logo.getpixel((w - 1, w - 1))[3] == 0  # corners transparent
    assert logo.getpixel((w // 2, w // 2)) == (240, 60, 180, 255)  # the logo itself untouched
    assert round_logo(Image.open(tmp_path / "logo.png"), square=True).getpixel((0, 0))[3] == 255  # --square keeps the square


def test_install_logo_writes_the_logo_and_the_youtube_studio_images(project: dict, tmp_path: Path) -> None:
    out = install_logo(project["settings"], _logo_on_black(tmp_path / "new.png"))
    sizes = {k: Image.open(p).size for k, p in out.items()}
    assert sizes == {"logo": (600, 600), "profile": (800, 800), "watermark": (150, 150)}
    with pytest.raises(ConfigError):
        install_logo(project["settings"], _logo_on_black(tmp_path / "tiny.png", size=120))


def test_stage_brand_puts_the_logo_where_the_renderer_finds_it(project: dict) -> None:
    s = project["settings"]
    brand = stage_brand(s)
    assert brand["name"] and brand["logo"].startswith("job_media/brand/logo_") and brand["logo"].endswith(".png")
    assert (bundle_dir(s) / "public" / brand["logo"]).is_file()
    assert 0.2 <= brand["watermarkOpacity"] <= 1.0


def test_a_missing_logo_is_an_error_and_an_empty_one_means_no_logo(project: dict) -> None:
    s = project["settings"]
    (s.root / "assets" / "brand" / "logo.png").unlink()
    with pytest.raises(ConfigError, match="does not exist"):
        stage_brand(s)
    s.data["channel"]["logo"] = ""  # channel.logo: ""
    assert stage_brand(s)["logo"] == ""
