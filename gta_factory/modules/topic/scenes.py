"""Storyboard of a comparison or a top list, built from the script without a model.

* Each item opens on its card: a split screen for a comparison (the other game on the
  left, GTA 6 on the right, "then" / "now" under each side, the item number and title),
  a numbered card over GTA 6 footage for a list.
* The rest of the item follows the narration sentence by sentence: what is said about
  the other game plays over that game's footage, what is said about GTA 6 over GTA 6
  footage, and a sentence that names both games goes back to the split screen.
* A small tag keeps the item in view ("03 · WEAPON LIMIT"), and every GTA 6 scene
  shows the fact-check status of the item's claims.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from ..footage.games import game, side_of
from ..script_generation.writer import split_sentences, word_count

STATUS_RANK = {"rumor": 0, "speculation": 1, "reported": 2, "confirmed": 3}


def compare_panels(width: int, height: int) -> dict[str, list[int]]:
    """Pixel rectangles [x, y, w, h] of the two footage panels (left = other game, right = GTA 6; stacked in 9:16)."""
    u = min(width, height) / 1080
    ev = lambda v: int(round(v / 2)) * 2  # noqa: E731  (even sizes for the encoder)
    if height > width:
        x, w, h, y = ev(48 * u), ev(width - 96 * u), ev(450 * u), ev(340 * u)
        return {"left": [x, y, w, h], "right": [x, ev(y + h + 20 * u), w, h]}
    w, h, y, x = ev((width - 180 * u) / 2), ev(560 * u), ev(232 * u), ev(70 * u)
    return {"left": [x, y, w, h], "right": [ev(width - x - w), y, w, h]}


def _groups(sents: list[str], target: int, cap: int) -> list[list[str]]:
    out: list[list[str]] = []
    cur: list[str] = []
    for s in sents:
        n = word_count(" ".join(cur + [s]))
        if cur and (n > cap or word_count(" ".join(cur)) >= target):
            out.append(cur)
            cur = []
        cur.append(s)
    if cur:
        out.append(cur)
    return out


def _status(ids: list[str], by_id: dict[str, dict[str, Any]]) -> str | None:
    st = [by_id[i]["classification"] for i in ids if i in by_id and not by_id[i].get("game_fact")]
    return min(st, key=lambda s: STATUS_RANK.get(s, 0)) if st else None


def _emphasis(title: str, narration: str) -> str:
    m = re.search(re.escape(title), narration, re.I)
    return m.group(0) if m else ""


def _lines(text: str) -> list[str]:
    words = [w for w in re.findall(r"[A-Za-z0-9$%'’.-]+", text) if w.upper() not in ("THE", "A", "AN", "OF", "AND")][:6]
    lines, cur = [], ""
    for w in words:
        if cur and (len(cur) + 1 + len(w) > 14 or len(cur.split()) >= 3):
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return [x.upper() for x in lines[:2]] or ["GTA 6"]


def build_topic_scenes(settings: Any, script: dict[str, Any], *, fmt: str, facts: list[dict[str, Any]], today: str) -> list[dict[str, Any]]:
    vertical = settings.format(fmt).get("aspect") == "9:16"
    prof = settings.video_profile(fmt)
    panels = compare_panels(int(prof["width"]), int(prof["height"]))
    wpm = float(settings.get("script.words_per_minute", 165))
    target = int(settings.get("topic.scene_words", 24)) if not vertical else 14
    cap = int(float(settings.get("script.max_scene_seconds", 14)) * wpm / 60)
    style = script.get("style") or "list"
    main = game(settings, "gta6")
    other = game(settings, script["vs"]) if style == "versus" and script.get("vs") else None
    by_id = {c["id"]: c for c in facts}
    items = {it["n"]: it for it in script.get("items") or []}
    total = len(items)
    oshort = (other or {}).get("short", "").upper()
    scenes: list[dict[str, Any]] = []

    def add(section: str, narration: str, template: str, data: dict[str, Any], *, chapter: str, gid: str = "gta6", status: str | None = None, claim_ids: list[str] | None = None, transition: str = "cut", sfx: str = "none", screen: str = "", visual: str = "", emphasis: str = "", item: int | None = None) -> None:
        ref = next((by_id[i] for i in claim_ids or [] if i in by_id and not by_id[i].get("game_fact")), None)
        sc = {
            "scene_number": len(scenes) + 1,
            "section": section,
            "chapter_title": chapter,
            "narration": narration,
            "template": template,
            "data": data,
            "emphasis": emphasis,
            "on_screen_text": screen,
            "visual_description": visual,
            "transition": transition,
            "sound_effect": sfx,
            "claim_ids": list(claim_ids or []),
            "status": status,
            "source_reference": f"{ref['original_publisher']} - {ref['original_source']}" if ref and gid == "gta6" and template != "compare" else "",
            "background": "grid",
            "footage_game": gid,
            **({"item": item} if item else {}),
        }
        if template == "compare":
            sc["footage_pair"] = [other["id"] if other else "gta6", "gta6"]
        scenes.append(sc)

    def compare_data(title: str, n: int | None, left_caption: str, right_caption: str, status: str | None) -> dict[str, Any]:
        return {"n": n, "total": total if n else None, "title": title.upper()[:34], "left": {"game": oshort, "caption": left_caption[:40]}, "right": {"game": main["short"].upper(), "caption": right_caption[:40], "status": status}, "panels": panels, "vertical": vertical}

    def side_scenes(section: str, chapter: str, sents: list[str], *, item: dict[str, Any] | None, start_side: str, ids: list[str]) -> None:
        """Group the sentences by the game they talk about, then by length."""
        runs: list[tuple[str, list[str]]] = []
        side = start_side
        for s in sents:
            got = side_of(s, other, main) if other else "gta6"
            side = got or side
            if runs and runs[-1][0] == side:
                runs[-1][1].append(s)
            else:
                runs.append((side, [s]))
        tag = f"{item['n']:02d} · {item['title'].upper()}" if item else ""
        for side, run in runs:
            for grp in _groups(run, target, cap):
                text = " ".join(grp)
                if side == "both" and other:
                    add(section, text, "compare", compare_data(item["title"] if item else chapter, None, "", "", _status(ids, by_id)), chapter=chapter, gid="gta6", claim_ids=ids, transition="wipe", sfx="whoosh",
                        screen=f"{oshort} vs {main['short'].upper()}", visual=f"Split screen: {other['short']} footage left, GTA 6 right.", item=item["n"] if item else None)
                    continue
                gid = other["id"] if side == "other" and other else "gta6"
                label = (other["short"] if gid != "gta6" else main["short"]).upper()
                add(section, text, "footage", {"game": label, "side": "other" if gid != "gta6" else "gta6", "tag": tag}, chapter=chapter, gid=gid,
                    status=_status(ids, by_id) if gid == "gta6" else None, claim_ids=ids, screen=f"{label} {tag}".strip(),
                    visual=f"Full-screen {label} footage" + (f" for {item['title']}" if item else "") + ".", item=item["n"] if item else None)

    def card_head(sents: list[str]) -> list[str]:
        """The sentences under an item's card: its opening, up to the first switch from one game to the other."""
        head: list[str] = []
        seen: set[str] = set()
        for s in sents:
            sd = side_of(s, other, main) if other else None
            if head and (word_count(" ".join(head)) >= target or word_count(" ".join(head + [s])) > cap or (sd and sd != "both" and seen and sd not in seen)):
                break
            head.append(s)
            if sd:
                seen.add(sd)
        return head

    for sec in script["sections"]:
        sid = sec["id"]
        sents = split_sentences(sec["narration"])
        ids = sec.get("claim_ids") or []
        if not sents:
            continue
        if sid == "hook":  # the opening line alone, then the title card on what follows
            groups = [sents[:1]] + ([sents[1:]] if len(sents) > 1 else [])
            first = " ".join(groups[0])
            if other:
                lines, accent, kicker = ["GTA 6", f"VS {oshort}"], oshort, "Side by side"
            else:
                lines = script.get("thumb_lines")[:2] if script.get("thumb_lines") else _lines(script["title"])
                accent, kicker = lines[-1].split()[-1], ""
            add("hook", first, "kinetic", {"lines": lines, "accent": accent, "kicker": kicker}, chapter="Intro", status=_status(ids, by_id), claim_ids=ids, transition="flash", sfx="impact", screen=" ".join(lines), visual="Kinetic hook over the footage.")
            rest = [s for g in groups[1:] for s in g]
            if rest:
                head = _groups(rest, target, cap)[0]
                d = date.fromisoformat(today[:10])
                add("hook", " ".join(head), "title", {"title": script["title"][:70], "subtitle": ((script.get("angle") or {}).get("thesis") or "")[:90], "date": f"{d:%B} {d.day}, {d.year}"}, chapter="Intro", claim_ids=ids, transition="slice", sfx="swell", screen=script["title"], visual="Title card.")
                side_scenes("hook", "Intro", rest[len(head):], item=None, start_side="gta6", ids=ids)
        elif sec.get("item") and sec["item"] in items:
            it = items[sec["item"]]
            groups = [card_head(sents)]
            groups.append(sents[len(groups[0]):])
            head = " ".join(groups[0])
            st = it.get("status") or _status(it["claim_ids"], by_id)
            if other:
                add(sid, head, "compare", compare_data(it["title"], it["n"], it.get("then") or "", it.get("now") or "", st), chapter=it["title"], claim_ids=ids, transition="slice", sfx="whoosh",
                    screen=f"#{it['n']} {it['title']} | {other['short']}: {it.get('then', '')} | GTA 6: {it.get('now', '')}", visual=f"Split screen, item {it['n']}: {other['short']} left, GTA 6 right.", emphasis=_emphasis(it["title"], head), item=it["n"])
                first_side = side_of(groups[0][-1], other, main) or "gta6"
                side_scenes(sid, it["title"], [s for g in groups[1:] for s in g], item=it, start_side="other" if first_side == "both" else first_side, ids=ids)
            else:
                add(sid, head, "item", {"n": it["n"], "total": total, "title": it["title"].upper()[:34], "kicker": it.get("kicker") or ""}, chapter=it["title"], status=st, claim_ids=ids, transition="slice", sfx="whoosh",
                    screen=f"#{it['n']} {it['title']}", visual=f"Entry {it['n']} card over GTA 6 footage.", emphasis=_emphasis(it["title"], head), item=it["n"])
                side_scenes(sid, it["title"], [s for g in groups[1:] for s in g], item=it, start_side="gta6", ids=ids)
        elif sid == "cta":
            text = " ".join(sents)
            add("cta", text, "outro", {"question": text[:110], "cta": "Tell us in the comments"}, chapter="Question", transition="slice", sfx="swell", screen=text, visual="Closing question.")
        else:  # verdict / wrap-up
            label = sec.get("label") or "Verdict"
            groups = _groups(sents, target, cap)
            if other:
                add(sid, " ".join(groups[0]), "compare", compare_data(label, None, "", "", _status(ids, by_id)), chapter=label, claim_ids=ids, transition="slice", sfx="whoosh", screen=f"{label}: {other['short']} vs GTA 6", visual="Split screen for the verdict.")
            else:
                add(sid, " ".join(groups[0]), "kinetic", {"lines": [label.upper()], "accent": label.upper(), "kicker": ""}, chapter=label, claim_ids=ids, transition="slice", sfx="impact", screen=label.upper(), visual="Kinetic wrap-up over the footage.")
            side_scenes(sid, label, [s for g in groups[1:] for s in g], item=None, start_side="gta6", ids=ids)
    return scenes
