"""Source selections for detail shots and choice recaps in the Ghost Plates Focus preset."""
from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ...app.core.errors import ProviderError

DETAIL_CUE = re.compile(r"\b(look at|notice|look closely|see that|watch the|zoom in|spot the)\b", re.I)
CHOICE_CUE = re.compile(r"\b(which|pick|choose|favorite|favourite|number)\b", re.I)


def frame_subjects(matches: list[dict[str, Any]], model: Any, *, aspect: float = 9 / 16) -> None:
    """Compare portrait crops of each selected source before cropping it for the phone screen."""
    from ..footage.autotag import frame_jpeg
    from .match import templated

    if model is None:
        return
    positions = np.linspace(0, 1, 13)
    contiguous = []
    for match in matches:
        prompt = model.embed_text(templated([match["concept"]]))[0]
        for sg in match["segments"]:
            times = [None]
            if sg["kind"] == "shot":
                times = [sg["start"] + (sg["end"] - sg["start"]) * f for f in (0.2, 0.8)]
            frame_scores = []
            axis = "focus_x"
            for at in times:
                try:
                    im = Image.open(io.BytesIO(frame_jpeg(Path(sg["path"]), at, width=768))).convert("RGB")
                except (ProviderError, OSError):
                    continue
                active = sg.get("crop") if at is not None else None
                h = min(im.height, round(im.width * active[1] / active[0])) if active else im.height
                y = (im.height - h) // 2
                w = min(im.width, round(h * aspect))
                if aspect > 1 and sg["kind"] == "shot" and h > im.width / aspect + 1:
                    view_h = round(im.width / aspect)
                    samples = []
                    # Reaction posts often sandwich the game between a facecam and blurred padding.
                    # Measure texture across each crop, so a sharp subtitle alone cannot outweigh a blurred panel.
                    grey = np.asarray(im.convert("L"), dtype=np.float32)
                    edges = abs(np.diff(grey, axis=1))
                    textured = (edges > 8).mean(axis=1)
                    coverage = []
                    for pos in positions:
                        top = y + round((h - view_h) * pos)
                        coverage.append(np.minimum(textured[top:top + view_h], .18).mean())
                        panel = im.crop((0, top, im.width, top + view_h))
                        samples.append(np.asarray(panel.resize((224, 224), Image.Resampling.BICUBIC)))
                    axis = "focus_y"
                    # Avoid the extreme top and bottom UI when candidate crops score similarly.
                    quality = np.asarray(coverage)
                    frame_scores.append(model.embed_images(samples) @ prompt + .13 * quality / max(.04, quality.max())
                                        - .055 * abs(positions - .5))
                    continue
                side = min(w, max(1, round(h * 0.4)))
                samples = []
                for pos in positions:
                    x = round((im.width - w) * pos + (w - side) / 2)
                    for height in (0.33, 0.58):
                        top = y + round(h * height - side / 2)
                        patch = im.crop((x, top, x + side, top + side)).resize((224, 224), Image.Resampling.BICUBIC)
                        samples.append(np.asarray(patch))
                scores = (model.embed_images(samples) @ prompt).reshape(len(positions), 2)
                frame_scores.append(scores.max(axis=1))
            if frame_scores:
                # Keep the subject in view at both ends of the cut.
                sg[axis] = float(positions[np.min(frame_scores, axis=0).argmax()])
            if axis == "focus_y" and match.get("status") == "source_link" and axis in sg:
                if not contiguous or contiguous[-1][-1]["path"] != sg["path"] or abs(contiguous[-1][-1]["end"] - sg["start"]) > .02:
                    contiguous.append([])
                contiguous[-1].append(sg)
    # A linked take continues through several sentences. Keep its crop steady at those boundaries.
    for group in contiguous:
        position = float(np.median([sg["focus_y"] for sg in group]))
        for sg in group:
            sg["focus_y"] = position


def focus_panels(matches: list[dict[str, Any]], before: int) -> list[dict[str, Any]]:
    """Two previous pictures accompany the live closing shot. Embed the stills in the render cache key."""
    from ..footage.autotag import frame_jpeg

    panels = []
    current = next(m for m in matches if m["beat"] == before)
    seen = {(sg["path"], sg["start"]) for sg in current["segments"]}
    candidates = sorted(matches, key=lambda m: (m.get("role") != "item", not m.get("specific")))
    for match in candidates:
        if match["beat"] >= before or match.get("role") in ("cta", "suspense") or not match["segments"]:
            continue
        src = match["segments"][0]
        key = (src["path"], src["start"])
        if key in seen:
            continue
        at = (src["start"] + src["end"]) / 2 if src["kind"] == "shot" else None
        try:
            jpg = frame_jpeg(Path(src["path"]), at, width=960)
        except ProviderError:
            continue
        label = re.sub(r"^[^:]{1,16}:\s*", "", match["text"]).strip(" .,!?")
        panels.append({"label": label if len(label.split()) <= 5 and len(label) <= 36 else "",
                       "image": "data:image/jpeg;base64," + base64.b64encode(jpg).decode("ascii"),
                       "beat": match["beat"] + 1, "credit": src.get("credit", "")})
        seen.add(key)
        if len(panels) == 2:
            break
    return panels
