"""The channel's identity on screen: its name, tagline and round logo.

``channel.logo`` (config.yaml) is the channel logo, a PNG with a transparent
background (round, shown as a circle). It is:

* the watermark in the top corner of every scene of every video (long and Shorts),
* the big mark of the end screen,
* the badge in the corner of every thumbnail.

``python -m gta_factory brand IMAGE`` installs a new logo from any square image
(a logo delivered on a black or white square is cut to its circle) and writes
the two files YouTube Studio asks for: the profile picture (800 x 800) and the
video watermark (150 x 150, Customization > Branding > Video watermark).
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from ..app.core.errors import ConfigError

BRAND_DIR = Path("assets") / "brand"
LOGO_NAME = "logo.png"
PROFILE_NAME = "youtube_profile_800.png"
WATERMARK_NAME = "youtube_watermark_150.png"


def logo_file(settings: Any) -> Path | None:
    """The configured logo file, or None when ``channel.logo`` is empty. A configured file that is missing is an error."""
    raw = str(settings.get("channel.logo") or "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    p = p if p.is_absolute() else settings.root / p
    if not p.is_file():
        raise ConfigError(
            f"The channel logo '{raw}' does not exist, so the videos would have no watermark.",
            hint='Put the file back, install one with `python -m gta_factory brand <image>`, or set channel.logo: "" in config.yaml to render without a logo.',
        )
    return p


def stage_brand(settings: Any) -> dict[str, Any]:
    """The ``brand`` prop of the templates: {"name", "tagline", "logo", "watermarkOpacity"}.

    The logo is copied into the renderer's public folder under a name made of its
    content hash, so changing the logo changes the rendered pictures (and the caches).
    """
    from .video_editing.render import bundle_dir

    out: dict[str, Any] = {
        "name": str(settings.get("channel.name") or ""),
        "tagline": str(settings.get("channel.tagline") or ""),
        "logo": "",
        "watermarkOpacity": max(0.2, min(1.0, float(settings.get("channel.watermark_opacity", 0.92)))),
    }
    src = logo_file(settings)
    if src:
        digest = hashlib.sha1(src.read_bytes()).hexdigest()[:12]
        target = bundle_dir(settings) / "public" / "job_media" / "brand"
        target.mkdir(parents=True, exist_ok=True)
        dest = target / f"logo_{digest}{src.suffix.lower()}"
        if not dest.exists():
            shutil.copyfile(src, dest)
        out["logo"] = f"job_media/brand/{dest.name}"
    return out


# ----------------------------------------------------------------------------- installing a new logo

def round_logo(image: Any, *, square: bool = False) -> Any:
    """A square RGBA logo with a transparent outside.

    A logo that already has a transparent background is only made square. A logo
    delivered on a plain square (black or white corners) is cut to the circle it
    contains, with an anti-aliased edge. ``square=True`` keeps the whole square.
    """
    from PIL import Image, ImageChops, ImageDraw

    im = image.convert("RGBA")
    w, h = im.size
    corners = [im.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    if square or all(c[3] < 16 for c in corners):
        box = im.getchannel("A").point(lambda v: 255 if v > 16 else 0).getbbox() or (0, 0, w, h)
        masked = False
    else:
        # what differs from the corner colour is the logo: its bounding box is the circle's
        bg = Image.new("RGB", im.size, corners[0][:3])
        box = ImageChops.difference(im.convert("RGB"), bg).convert("L").point(lambda v: 255 if v > 40 else 0).getbbox() or (0, 0, w, h)
        masked = True
    side = max(box[2] - box[0], box[3] - box[1])
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    crop = im.crop((round(cx - side / 2), round(cy - side / 2), round(cx - side / 2) + side, round(cy - side / 2) + side))
    if masked:
        k = 4  # draw the circle 4x larger, then shrink it: a smooth edge
        mask = Image.new("L", (side * k, side * k), 0)
        ImageDraw.Draw(mask).ellipse((k, k, side * k - k - 1, side * k - k - 1), fill=255)
        mask = mask.resize((side, side), Image.LANCZOS)
        crop.putalpha(ImageChops.multiply(crop.getchannel("A"), mask))
    return crop


def install_logo(settings: Any, image: Path, *, square: bool = False) -> dict[str, Path]:
    """Make ``assets/brand/logo.png`` (used by the videos) and the two YouTube Studio images from ``image``."""
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise ConfigError("Pillow is needed to prepare the logo.", hint="Run ./install.sh again (or `pip install pillow`).") from exc

    try:
        src = Image.open(image)
        src.load()
    except (OSError, ValueError) as exc:
        raise ConfigError(f"'{image}' is not an image this tool can read ({exc}).", hint="Use a PNG, JPG or WEBP file.") from exc
    logo = round_logo(src, square=square)
    if logo.size[0] < 300:
        raise ConfigError(f"The logo is only {logo.size[0]} px wide.", hint="Use an image of at least 800 x 800 px: YouTube shows the profile picture at 800 x 800.")
    folder = settings.root / BRAND_DIR
    folder.mkdir(parents=True, exist_ok=True)
    out = {
        "logo": folder / LOGO_NAME,
        "profile": folder / PROFILE_NAME,
        "watermark": folder / WATERMARK_NAME,
    }
    logo.resize((min(1024, logo.size[0]),) * 2, Image.LANCZOS).save(out["logo"], optimize=True)
    logo.resize((800, 800), Image.LANCZOS).save(out["profile"], optimize=True)
    logo.resize((150, 150), Image.LANCZOS).save(out["watermark"], optimize=True)  # YouTube: 150 x 150, under 1 MB
    return out
