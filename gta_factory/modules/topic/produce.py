"""Packaging of a comparison or a top list: thumbnails, description sections, review notes."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ...app.core.utils import read_json
from ..thumbnail_generation.concepts import design_thumbnails


def job_facts(settings: Any, job_id: str, fact_check: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The fact-check of a topic job plus the other game's facts: everything its script may use."""
    from ...app.database.db import get_db
    from ..footage.games import game
    from .writer import game_claims

    vs = ((get_db(settings.db_path).get_job(job_id) or {}).get("options") or {}).get("vs")
    facts = read_json(settings.jobs_dir / job_id / "research" / "game_facts.json", []) or []
    return fact_check + (game_claims(facts, game(settings, vs)) if vs and facts else [])


def thumbnail_concepts(llm: Any, settings: Any, *, script: dict[str, Any], fact_check: list[dict[str, Any]], other: dict[str, Any] | None, out_dir: Path, log: Callable[[str], None] = lambda m: None) -> list[dict[str, Any]]:
    """Three concepts: the signature one first (split "GTA 6 vs ..." with a picture of each game, or the
    number of entries), then two from the usual designer, each over a real GTA 6 picture.

    Each concept gets ``photo_file`` (and ``photo2_file`` for the split) when the library has pictures.
    """
    from ..footage.games import game_settings
    from ..footage.select import thumbnail_photos

    brand = settings.get("channel.name", "NEON DISPATCH")
    count = int(settings.get("thumbnail.concepts", 3))
    items = script.get("items") or []
    titles = " ".join(it["title"] for it in items)
    story = {"working_title": script["title"], "angle": script.get("angle") or {}}
    others = design_thumbnails(llm, settings, script=script, story=story, fact_check=fact_check, log=log)
    if other:
        lines = ["GTA 6", f"VS {other['short'].upper()}"]
        first = {"concept": "versus", "lines": lines, "accent": "VS", "status": None, "motif": "controller", "bigText": "", "label": other["short"].upper()[:26], "brand": brand, "color": "coral", "calendar": None}
    else:
        thumb = script.get("thumb_lines") or []
        label = (thumb[1] if len(thumb) > 1 else thumb[0] if thumb else script["title"]).upper()[:26]
        first = {"concept": "number", "lines": [label], "accent": "", "status": None, "motif": "controller", "bigText": str(len(items)), "label": label, "brand": brand, "color": "sun", "calendar": None}
    concepts = [{"props": first, "why": "the signature layout of this kind of video", "notes": [], "lead_claim_id": None}] + others[: max(0, count - 1)]
    pics = thumbnail_photos(settings, f"{script['title']} {titles}", len(concepts), out_dir=out_dir)
    for c, pic in zip(concepts, pics):
        c["photo_file"] = str(pic)
    if other:
        theirs = thumbnail_photos(game_settings(settings, other["id"]), f"{script['title']} {titles}", 1, out_dir=out_dir / other["id"])
        if theirs:
            concepts[0]["photo2_file"] = str(theirs[0])
    return concepts


def description_sections(script: dict[str, Any], other: dict[str, Any] | None) -> tuple[list[tuple[str, list[str]]], str]:
    items = script.get("items") or []
    if other:
        rows = [f"{it['n']}. {it['title']}: {other['short']}, {it.get('then', '')} / GTA 6, {it.get('now', '')}" for it in items]
        how = (
            f"Every GTA 6 claim is checked against the sources below and labelled CONFIRMED, REPORTED, RUMOR or SPECULATION. "
            f"What is said about {other['name']} describes the released game."
        )
        return [("THE COMPARISON, SYSTEM BY SYSTEM", rows)], how
    rows = [f"{it['n']}. {it['title']}" + (f" ({it['kicker']})" if it.get("kicker") else "") for it in items]
    return [("THE LIST", rows)], "Every GTA 6 claim is checked against the sources below and labelled CONFIRMED, REPORTED, RUMOR or SPECULATION."


def extra_tags(script: dict[str, Any], other: dict[str, Any] | None) -> list[str]:
    if other:
        s = other["short"].lower()
        return [f"gta 6 vs {s}", f"{s} vs gta 6", other["name"].lower(), "gta 6 comparison"]
    return ["gta 6 tips", "gta 6 guide"]


def review_points(script: dict[str, Any], facts: list[dict[str, Any]], other: dict[str, Any] | None, facts_file: str = "") -> str:
    by_id = {c["id"]: c for c in facts}
    rows = []
    for it in script.get("items") or []:
        gta = [f"{i} {by_id[i]['classification'].upper()} ({by_id[i]['original_publisher']})" for i in it["claim_ids"] if i in by_id]
        theirs = [f"{i} « {by_id[i]['claim'][:90]} »" for i in it.get("game_fact_ids") or [] if i in by_id]
        rows.append(f"    - **#{it['n']} {it['title']}** : GTA 6 → {', '.join(gta)}" + (f" ; {other['short']} → {'; '.join(theirs)}" if theirs and other else ""))
    head = "\n- [ ] Relire `script.md` : chaque item ne dit sur GTA 6 que ce que disent ses sources (statut à l'écran)"
    if other:
        head += f", et sur {other['short']} que ce que dit `{facts_file or 'knowledge/games/' + other['id'] + '.yaml'}` (le jeu sorti : corrige ce fichier si un fait est faux)"
    return head + ".\n" + "\n".join(rows)
