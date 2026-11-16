"""Rights review gate: every visual and sound in the video needs a documented licence.

What can appear in a video
--------------------------
* Original motion graphics (the Remotion templates) and the synthesised audio
  library: owned by the operator, always approved.
* Licensed stock photos (Pexels / Pixabay) found for ``broll`` scenes: approved,
  credited on screen and in the description.
* Operator assets in ``assets/licensed_visuals/``: approved only with a licence
  sidecar ``<file>.license.json`` next to the file, e.g.::

      {"license": "CC BY 4.0", "source_url": "https://...", "commercial_use": true,
       "attribution_required": true, "attribution": "Photo: Jane Doe / CC BY 4.0",
       "kind": "photo", "tags": ["miami", "skyline", "night"]}

  ``kind: "official_press"`` (publisher press material) is refused in ``strict``
  mode and flagged ``needs_human_review`` in ``editorial`` mode, because
  commercial rights for press material are never assumed.

Anything that fails the gate is not used: the scene falls back to original
kinetic typography, so a rights problem can never block or leak into a render.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from ...app.core.utils import sha1, tokens
from ...providers.stock import download_photo, get_stock_providers
from ..script_generation.scenes import _fallback_scene
from ..visual_research.catalog import on_screen_text

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}

FONTS = [
    {"name": "Barlow Condensed", "license": "SIL Open Font License 1.1", "source_url": "https://fonts.google.com/specimen/Barlow+Condensed"},
    {"name": "Inter", "license": "SIL Open Font License 1.1", "source_url": "https://fonts.google.com/specimen/Inter"},
    {"name": "JetBrains Mono", "license": "SIL Open Font License 1.1", "source_url": "https://fonts.google.com/specimen/JetBrains+Mono"},
]
VOICE_LICENSES = {
    "kokoro": ("Kokoro-82M stock voice (Apache-2.0 model weights)", True, "approved"),
    "openai": ("OpenAI TTS stock voice (output owned by the API customer under OpenAI's terms)", True, "approved"),
    "elevenlabs": ("ElevenLabs voice from the operator's account (operator confirms the voice licence and plan allow commercial use)", True, "needs_human_review"),
}


def _blocked(text: str, blocked: list[str]) -> str | None:
    low = (text or "").lower()
    for word in blocked:
        if re.search(rf"\b{re.escape(word.lower())}\b", low):
            return word
    return None


def load_operator_assets(folder: Path) -> list[dict[str, Any]]:
    """Every image in ``assets/licensed_visuals`` with its sidecar (or ``sidecar: None``)."""
    out = []
    if not folder.exists():
        return out
    for p in sorted(folder.rglob("*")):
        if p.suffix.lower() not in IMAGE_EXT:
            continue
        sidecar = p.with_name(p.name + ".license.json")
        meta = None
        if sidecar.exists():
            try:
                meta = json.loads(sidecar.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                meta = {"_invalid": True}
        out.append({"asset_id": p.stem, "path": p, "sidecar": meta})
    return out


def _check_operator_asset(asset: dict[str, Any], mode: str, require_sidecar: bool, blocked: list[str]) -> tuple[str, str]:
    """Return (review_status, reason)."""
    meta = asset["sidecar"]
    if meta is None:
        return ("rejected", "No licence sidecar (<file>.license.json).") if require_sidecar else ("needs_human_review", "No licence sidecar; the operator must confirm the rights.")
    if meta.get("_invalid"):
        return "rejected", "The licence sidecar is not valid JSON."
    hit = _blocked(" ".join([asset["path"].name, meta.get("source_url", ""), " ".join(meta.get("tags") or []), meta.get("notes", "")]), blocked)
    if hit:
        return "rejected", f"Blocked keyword '{hit}' in the asset metadata."
    if not meta.get("license"):
        return "rejected", "The sidecar does not name a licence."
    if meta.get("commercial_use") is not True:
        return "rejected", "The licence does not allow commercial use (commercial_use must be true)."
    if meta.get("attribution_required") and not meta.get("attribution"):
        return "rejected", "Attribution is required but the sidecar has no attribution text."
    if meta.get("kind") == "official_press":
        if mode != "editorial":
            return "rejected", "Official press material is not allowed in strict mode."
        return "needs_human_review", "Official press material: commercial rights are not assumed; confirm before publishing."
    return "approved", "Licence documented in the sidecar."


def review_assets(
    settings: Any,
    scenes: list[dict[str, Any]],
    *,
    media_dir: Path,
    tts_provider: str,
    vertical: bool,
    log: Callable[[str], None] = lambda m: None,
) -> dict[str, Any]:
    """Resolve and review every asset. Mutates ``scenes`` (resolved b-roll or fallback) and returns the review."""
    mode = str(settings.get("rights_review.mode", "strict"))
    blocked = [str(w) for w in settings.get("rights_review.blocked_keywords", []) or []]
    require_sidecar = bool(settings.get("rights_review.require_license_sidecar_for_user_assets", True))
    operator_assets = load_operator_assets(settings.assets_dir / "licensed_visuals")
    stock = get_stock_providers(settings)
    ledger: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    used_media: set[str] = set()

    def fallback(sc: dict[str, Any], reason: str) -> None:
        sc["template"], sc["data"] = _fallback_scene(sc["narration"], sc.get("emphasis", ""))
        sc["on_screen_text"] = on_screen_text("kinetic", sc["data"])
        sc.pop("media_file", None)
        sc.pop("media_credit", None)
        decisions.append({"scene_number": sc["scene_number"], "outcome": "fallback_to_kinetic", "reason": reason})

    for sc in scenes:
        if sc["template"] != "broll":
            continue
        data = sc.get("data") or {}
        query = data.get("query") or ""
        hit = _blocked(" ".join([query, data.get("caption", ""), data.get("asset_id", "")]), blocked)
        if hit:
            fallback(sc, f"Blocked keyword '{hit}' in the b-roll request.")
            continue
        chosen: dict[str, Any] | None = None
        # 1) an operator asset named by id, or tagged with the query words
        candidates = []
        if data.get("asset_id"):
            candidates = [a for a in operator_assets if a["asset_id"] == data["asset_id"]]
        elif query:
            q = set(tokens(query))
            scored = sorted(((len(q & set(tokens(" ".join((a["sidecar"] or {}).get("tags") or []) + " " + a["asset_id"].replace("_", " ")))), a) for a in operator_assets), key=lambda x: -x[0])
            candidates = [a for s, a in scored if s >= max(1, len(q) // 2) and str(a["path"]) not in used_media]
        for a in candidates:
            status, reason = _check_operator_asset(a, mode, require_sidecar, blocked)
            meta = a["sidecar"] or {}
            entry = {
                "asset_id": f"op-{a['asset_id']}",
                "kind": meta.get("kind") or "photo",
                "file_path": str(a["path"]),
                "origin_url": meta.get("source_url") or "",
                "license": meta.get("license") or "",
                "commercial_use": meta.get("commercial_use") is True,
                "attribution_required": bool(meta.get("attribution_required")),
                "attribution": meta.get("attribution") or "",
                "review_status": status,
                "reason": reason,
                "scene_number": sc["scene_number"],
            }
            ledger.append(entry)
            if status != "rejected":
                chosen = entry
                break
        # 2) licensed stock
        if chosen is None and query and stock:
            for provider in stock:
                try:
                    photos = provider.search(query, vertical=vertical)
                except Exception as exc:
                    log(f"stock search on {provider.name} failed: {getattr(exc, 'message', exc)}")
                    continue
                for photo in photos:
                    if photo.page_url in used_media or _blocked(photo.page_url, blocked):
                        continue
                    try:
                        path = download_photo(photo, media_dir)
                    except Exception as exc:
                        log(f"stock download failed: {getattr(exc, 'message', exc)}")
                        continue
                    chosen = {
                        "asset_id": f"{photo.provider}-{photo.photo_id}",
                        "kind": "stock_photo",
                        "file_path": str(path),
                        "origin_url": photo.page_url,
                        "license": photo.license,
                        "license_url": photo.license_url,
                        "commercial_use": photo.commercial_use,
                        "attribution_required": photo.attribution_required,
                        "attribution": f"Photo: {photo.author} / {photo.provider.capitalize()}" if photo.author else f"Photo: {photo.provider.capitalize()}",
                        "review_status": "approved",
                        "reason": f"{photo.license} allows commercial use in video without attribution; credited anyway.",
                        "scene_number": sc["scene_number"],
                    }
                    ledger.append(chosen)
                    used_media.add(photo.page_url)
                    break
                if chosen:
                    break
        if chosen is None:
            fallback(sc, "No licensed visual found (no matching operator asset with a valid sidecar and no stock result).")
            continue
        if chosen["kind"] != "stock_photo":
            used_media.add(chosen["file_path"])
        sc["media_file"] = chosen["file_path"]
        sc["media_credit"] = chosen.get("attribution") or ""
        sc["asset_id"] = chosen["asset_id"]
        decisions.append({"scene_number": sc["scene_number"], "outcome": "media", "asset_id": chosen["asset_id"], "review_status": chosen["review_status"]})

    # --- the rest of the production: always documented
    mood = str(settings.get("audio.music_mood", "pulse"))
    lib = settings.assets_dir
    ledger.append({"asset_id": f"music-{mood}", "kind": "music", "file_path": str(lib / "music" / f"{mood}.wav"), "origin_url": "", "license": "Original, procedurally synthesised by this project", "commercial_use": True, "attribution_required": False, "review_status": "approved", "reason": "Generated from oscillators and noise; no samples."})
    for sfx in sorted({sc.get("sound_effect") for sc in scenes if sc.get("sound_effect") not in (None, "none")}):
        ledger.append({"asset_id": f"sfx-{sfx}", "kind": "sound_effect", "file_path": str(lib / "sound_effects" / f"{sfx}.wav"), "origin_url": "", "license": "Original, procedurally synthesised by this project", "commercial_use": True, "attribution_required": False, "review_status": "approved", "reason": "Generated from oscillators and noise; no samples."})
    for f in FONTS:
        ledger.append({"asset_id": f"font-{f['name'].lower().replace(' ', '-')}", "kind": "font", "file_path": "node_modules/@fontsource", "origin_url": f["source_url"], "license": f["license"], "commercial_use": True, "attribution_required": False, "review_status": "approved", "reason": "OFL fonts may be embedded in videos."})
    ledger.append({"asset_id": "graphics-templates", "kind": "motion_graphics", "file_path": "templates/", "origin_url": "", "license": "Original templates of this project", "commercial_use": True, "attribution_required": False, "review_status": "approved", "reason": "All scene graphics are drawn in code; no third-party artwork, logos or game footage."})
    v_license, v_commercial, v_status = VOICE_LICENSES.get(tts_provider, ("Unknown voice provider", False, "needs_human_review"))
    ledger.append({"asset_id": f"voice-{tts_provider}", "kind": "voice", "file_path": "", "origin_url": "", "license": v_license, "commercial_use": v_commercial, "attribution_required": False, "review_status": v_status, "reason": "Synthetic narration of an original script; no third-party voice is cloned."})

    for e in ledger:
        e.setdefault("asset_id", "asset-" + sha1(json.dumps(e, sort_keys=True, default=str))[:10])
    used = [e for e in ledger if e["review_status"] != "rejected" and (e.get("scene_number") is None or any(d.get("asset_id") == e["asset_id"] for d in decisions))]
    pending = [e for e in used if e["review_status"] == "needs_human_review"]
    rejected = [e for e in ledger if e["review_status"] == "rejected"]
    verdict = "needs_human_review" if pending else "approved"
    return {
        "mode": mode,
        "verdict": verdict,
        "assets": used,
        "rejected": rejected,
        "decisions": decisions,
        "credits": sorted({e["attribution"] for e in used if e.get("attribution")}),
        "summary": f"{len(used)} asset(s) cleared, {len(pending)} awaiting human review, {len(rejected)} rejected.",
    }
