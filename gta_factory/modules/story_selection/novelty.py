"""Story selection: NEWS NOVELTY SCORE, duplicate detection and the editorial angle.

Deterministic parts
* overlap with every previously produced story (claim-level token similarity),
* recency from publication dates,
* the final weighted score and the produce / update / skip decision.

LLM part
* editorial judgement of audience relevance, interest and player impact,
* which claims are genuinely new compared with earlier stories,
* the angle (three candidates, one chosen) that makes the video original.
"""
from __future__ import annotations

from typing import Any

from ...app.core.utils import clamp, jaccard, tokens
from ...providers.llm import LLMProvider
from ..fact_checking.verify import days_since

WEIGHTS = {"originality": 0.24, "recency": 0.18, "audience_relevance": 0.18, "potential_interest": 0.16, "player_impact": 0.14, "not_already_covered": 0.10}

EDITOR_SYSTEM = """You are the managing editor of an independent GTA VI news channel on YouTube.
Your job is to decide whether a story deserves a video and, if so, to find the angle that adds real
editorial value: context, comparison, consequences for players. You dislike hype and never promise more
than the sources establish. You judge strictly from the material provided."""

EDITOR_PROMPT = """Today's date: {today}
Requested format: {format_label} (target narration length about {target_seconds} seconds)

STORY
Headline: {headline}
Summary: {summary}

FACT-CHECKED CLAIMS
{claims}

ADDITIONAL VERIFIED CONTEXT
{context}

PREVIOUSLY PRODUCED VIDEOS ON THIS CHANNEL (most similar first)
{previous}

Return a JSON object:
"scores": each 0-100 with a one-sentence "why":
    "originality": {{"score": n, "why": "..."}}   (is there genuinely new information vs. what fans already know?)
    "audience_relevance": {{"score": n, "why": "..."}}   (how much do GTA 6 followers care?)
    "potential_interest": {{"score": n, "why": "..."}}   (curiosity / discussion potential without clickbait)
    "player_impact": {{"score": n, "why": "..."}}   (does it change what players will do, buy or expect?)
"new_claim_ids": ids of claims NOT already covered by a previous video (all ids if there are no previous videos)
"already_covered_claim_ids": ids substantially covered before
"angles": exactly 3 candidate editorial angles, each:
    {{"title": short internal name, "thesis": one sentence the video argues or explains, "why_original": what this adds beyond repeating the article,
      "lead_claim_ids": [ids that carry the angle], "risk": what could make it misleading and how to avoid it}}
"chosen_angle": index 0-2 of the best angle. It must differ in thesis from the previous videos' angles.
"working_title": a factual working title (max 70 characters)
"hook_idea": the single most interesting VERIFIED point OF THE MAIN STORY to open with (one sentence, cite the claim id in brackets; a MAIN STORY claim when the list marks them)
"what_we_do_not_know": 2-4 open questions the script should state honestly
"recommended_format": one of {formats}
"editor_notes": 1-3 sentences for the writer (tone, things to avoid)
"""


def story_fingerprint(analysis: dict[str, Any], fact_check: list[dict[str, Any]]) -> dict[str, Any]:
    ents = analysis.get("entities") or {}
    flat = sorted({str(e).lower() for group in ents.values() if isinstance(group, list) for e in group})
    return {
        "headline_tokens": sorted(set(tokens(analysis.get("story_headline", "")))),
        "entities": flat,
        "tags": sorted({str(t).lower() for t in analysis.get("topic_tags") or []}),
        "dates": sorted({c["event_date"] for c in fact_check if c.get("event_date")}),
    }


def claim_overlap(new_claims: list[str], old_claims: list[str], threshold: float = 0.42) -> tuple[float, list[int]]:
    """Share of new claims that match some old claim. Returns (share, indexes of matched new claims)."""
    if not new_claims or not old_claims:
        return 0.0, []
    old_tok = [set(tokens(c)) for c in old_claims]
    matched = []
    for i, c in enumerate(new_claims):
        t = set(tokens(c))
        if max((jaccard(t, o) for o in old_tok), default=0.0) >= threshold:
            matched.append(i)
    return len(matched) / len(new_claims), matched


def compare_with_history(analysis: dict[str, Any], fact_check: list[dict[str, Any]], stories: list[dict[str, Any]]) -> dict[str, Any]:
    fp = story_fingerprint(analysis, fact_check)
    new_claims = [c["claim"] for c in fact_check]
    rows = []
    for s in stories:
        old_fp = s.get("fingerprint") or {}
        share, matched = claim_overlap(new_claims, s.get("claims") or [])
        ent = jaccard(fp["entities"], old_fp.get("entities") or [])
        head = jaccard(fp["headline_tokens"], old_fp.get("headline_tokens") or [])
        similarity = round(0.7 * share + 0.15 * ent + 0.15 * head, 3)
        rows.append({"job_id": s["job_id"], "headline": s["headline"], "angle": s.get("angle"), "created_at": s["created_at"], "similarity": similarity, "claim_overlap": round(share, 3), "matched_claim_indexes": matched, "claims": s.get("claims") or []})
    rows.sort(key=lambda r: -r["similarity"])
    return {"fingerprint": fp, "matches": rows[:5], "max_similarity": rows[0]["similarity"] if rows else 0.0}


def recency_score(articles: list[dict[str, Any]], fact_check: list[dict[str, Any]], today: str, stale_after: int) -> tuple[int, str]:
    ages = [d for d in (days_since(a.get("published_at"), today) for a in articles) if d is not None]
    old_share = sum(1 for c in fact_check if c.get("is_old_news")) / max(1, len(fact_check))
    if not ages:
        base, why = 55, "No publication date exposed by the sources; recency could not be verified."
    else:
        age = max(0, min(ages))
        base = 100 if age <= 1 else 92 if age <= 2 else 80 if age <= 4 else 62 if age <= 7 else 40 if age <= stale_after else 18
        why = f"Newest source published {age} day(s) ago."
    if old_share > 0:
        base = int(base * (1 - 0.6 * old_share))
        why += f" {int(old_share * 100)}% of the claims were already public more than 10 days ago."
    return int(clamp(base, 0, 100)), why


def select_story(
    llm: LLMProvider,
    settings: Any,
    *,
    analysis: dict[str, Any],
    fact_check: list[dict[str, Any]],
    verification: dict[str, Any],
    articles: list[dict[str, Any]],
    stories: list[dict[str, Any]],
    fmt: str,
    today: str,
) -> dict[str, Any]:
    history = compare_with_history(analysis, fact_check, stories)
    fmt_cfg = settings.format(fmt)
    claims_txt = "\n".join(
        f"{c['id']} [{c['classification'].upper()}, confidence {c['confidence']}{', OLD NEWS' if c['is_old_news'] else ''}{', MAIN STORY' if c.get('relevance') == 'core' else ''}] {c['claim']} (source: {c['original_publisher']}; attributed to {c.get('attributed_to')})"
        for c in fact_check
    )
    ctx_txt = "\n".join(f"- {f['fact']} ({f['publisher']})" for f in verification.get("additional_facts") or []) or "(none)"
    prev_txt = (
        "\n".join(
            f"- [{m['created_at'][:10]}] \"{m['headline']}\" | angle: {m['angle']} | similarity {m['similarity']:.2f}\n  claims covered: " + " / ".join(m["claims"][:8])
            for m in history["matches"]
        )
        or "(none yet - this is the first video on this story)"
    )

    def validate(d: Any) -> str | None:
        if not isinstance(d.get("angles"), list) or len(d["angles"]) < 1:
            return "angles must contain 3 candidates"
        if not isinstance(d.get("chosen_angle"), int) or not 0 <= d["chosen_angle"] < len(d["angles"]):
            return "chosen_angle must index into angles"
        for k in ("originality", "audience_relevance", "potential_interest", "player_impact"):
            if not isinstance((d.get("scores") or {}).get(k, {}).get("score"), (int, float)):
                return f"scores.{k}.score must be a number"
        return None

    ed = llm.complete_json(
        EDITOR_SYSTEM,
        EDITOR_PROMPT.format(
            today=today,
            format_label=fmt_cfg.get("label", fmt),
            target_seconds=fmt_cfg.get("target_seconds"),
            headline=analysis.get("story_headline"),
            summary=analysis.get("summary"),
            claims=claims_txt,
            context=ctx_txt,
            previous=prev_txt,
            formats=", ".join(settings.get("formats", {}).keys()),
        ),
        max_tokens=4000,
        label="story_selection",
        required_keys=("scores", "angles", "chosen_angle", "working_title", "hook_idea"),
        validate=validate,
    )

    rec, rec_why = recency_score(articles, fact_check, today, int(settings.get("novelty.stale_after_days", 10)))
    sim = history["max_similarity"]
    valid_ids = {c["id"] for c in fact_check}
    new_ids = [i for i in ed.get("new_claim_ids") or [] if i in valid_ids]
    covered_ids = [i for i in ed.get("already_covered_claim_ids") or [] if i in valid_ids]
    if not stories:
        new_ids, covered_ids = sorted(valid_ids, key=lambda x: int(x[1:])), []
    # token-level matches are a floor for "already covered": the editor cannot un-cover them
    if history["matches"]:
        for idx in history["matches"][0]["matched_claim_indexes"]:
            cid = fact_check[idx]["id"]
            if cid in new_ids:
                new_ids.remove(cid)
            if cid not in covered_ids:
                covered_ids.append(cid)
    not_covered = int(round(100 * (1 - sim)))
    sub = {
        "originality": {"score": int(clamp(ed["scores"]["originality"]["score"], 0, 100)), "why": ed["scores"]["originality"].get("why", "")},
        "recency": {"score": rec, "why": rec_why},
        "audience_relevance": {"score": int(clamp(ed["scores"]["audience_relevance"]["score"], 0, 100)), "why": ed["scores"]["audience_relevance"].get("why", "")},
        "potential_interest": {"score": int(clamp(ed["scores"]["potential_interest"]["score"], 0, 100)), "why": ed["scores"]["potential_interest"].get("why", "")},
        "player_impact": {"score": int(clamp(ed["scores"]["player_impact"]["score"], 0, 100)), "why": ed["scores"]["player_impact"].get("why", "")},
        "not_already_covered": {"score": not_covered, "why": f"Highest similarity to an earlier video: {sim:.2f}." if stories else "No earlier video on this story."},
    }
    if stories and sim >= float(settings.get("novelty.update_threshold", 0.35)):
        # originality cannot exceed the share of claims that are actually new
        share_new = len(new_ids) / max(1, len(valid_ids))
        capped = int(min(sub["originality"]["score"], 25 + 75 * share_new))
        if capped < sub["originality"]["score"]:
            sub["originality"] = {"score": capped, "why": sub["originality"]["why"] + f" Capped: only {len(new_ids)} of {len(valid_ids)} claims are new for this channel."}
    score = int(round(sum(sub[k]["score"] * w for k, w in WEIGHTS.items())))

    dup_t = float(settings.get("novelty.duplicate_threshold", 0.72))
    upd_t = float(settings.get("novelty.update_threshold", 0.35))
    min_score = int(settings.get("novelty.min_score_to_produce", 45))
    if stories and sim >= dup_t and len(new_ids) <= 1:
        decision, reason = "skip_duplicate", f"Same story as job {history['matches'][0]['job_id']} (similarity {sim:.2f}) with no meaningful new information."
    elif stories and sim >= upd_t:
        if new_ids:
            decision, reason = "produce_update", f"Overlaps an earlier video (similarity {sim:.2f}) but adds {len(new_ids)} new claim(s): the script must lead with what is new and only recap the rest."
        else:
            decision, reason = "skip_duplicate", f"Overlaps an earlier video (similarity {sim:.2f}) and adds nothing new."
    elif score < min_score:
        decision, reason = "skip_low_novelty", f"NEWS NOVELTY SCORE {score} is below the production threshold of {min_score}."
    else:
        decision, reason = "produce", "New story for this channel."

    angle = ed["angles"][ed["chosen_angle"]]
    return {
        "novelty_score": score,
        "sub_scores": sub,
        "weights": WEIGHTS,
        "decision": decision,
        "decision_reason": reason,
        "similar_stories": [{k: m[k] for k in ("job_id", "headline", "angle", "created_at", "similarity", "claim_overlap")} for m in history["matches"]],
        "previous_angles": [m["angle"] for m in history["matches"] if m.get("angle")],
        "fingerprint": history["fingerprint"],
        "new_claim_ids": new_ids,
        "already_covered_claim_ids": covered_ids,
        "angles": ed["angles"],
        "chosen_angle_index": ed["chosen_angle"],
        "angle": angle,
        "working_title": ed.get("working_title"),
        "hook_idea": ed.get("hook_idea"),
        "open_questions": ed.get("what_we_do_not_know") or [],
        "recommended_format": ed.get("recommended_format") or fmt,
        "editor_notes": ed.get("editor_notes") or "",
    }
