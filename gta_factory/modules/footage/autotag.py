"""Tag the footage automatically with a free local vision model (Ollama), so the edit can match it to the narration.

For every usable trailer shot (its middle frame) and every screenshot, the model
is shown the picture and the list of concepts (``vocabulary.py``: characters,
places, police, beach, alligator...) and answers which ones it clearly sees,
plus a few other visible things and a one-line caption. Results are cached in
``assets/gta6_footage/autotags.json`` (one entry per shot / photo), so only new
footage is ever looked at again, and an interrupted run resumes where it stopped.

Your own tags (file names, sidecars, `footage tag`, the dashboard) always count
more than what the model saw: fix a wrong guess by tagging the file yourself.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

import httpx

from ...app.core.errors import ConfigError, ProviderError
from ...app.core.utils import read_json, write_json
from .library import autotags_path, build_index, footage_dir, photo_key, shot_key, still_shot_key
from .vocabulary import GENERIC_OBJECTS, Vocabulary, load_vocabulary, phrase_tokens

# open-weight vision models served by Ollama for free (best first by machine memory)
VISION_LADDER = [(15, "qwen2.5vl:7b", "6 GB"), (0, "qwen2.5vl:3b", "3.2 GB")]

PROMPT = """This picture is a frame from an official Grand Theft Auto VI trailer or an official screenshot.
Choose every label below that clearly applies to what is VISIBLE in the picture (not what might be implied).
Use a character label only when you are confident it is that character.

LABELS
{labels}

Also give up to 6 other clearly visible things as single lowercase English nouns ("objects"),
and a one-sentence caption of the picture.
Answer with JSON only: {{"tags": ["label", ...], "objects": ["noun", ...], "caption": "..."}}"""


def vision_model(settings: Any) -> str:
    wanted = str(settings.get("footage.vision_model") or "auto").strip()
    if wanted != "auto":
        return wanted
    from ...providers.llm.local import total_ram_gb

    ram = total_ram_gb()
    return next(tag for need, tag, _ in VISION_LADDER if ram >= need)


def _host(settings: Any) -> str:
    import os

    host = str(settings.get("providers.llm.ollama.host") or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
    return host if host.startswith("http") else "http://" + host


def _installed(have: list[str], tag: str) -> bool:
    return any(h == tag or h == f"{tag}:latest" or (":" not in tag and h.split(":")[0] == tag) for h in have)


def labels_prompt(vocab: Vocabulary) -> str:
    rows = []
    for cid, c in vocab.concepts.items():
        look = str(c.get("look") or "").strip()
        if look:
            rows.append(f"- {cid}: {look}")
    return "\n".join(rows)


def ollama_tagger(settings: Any, model: str | None = None, *, transport: httpx.BaseTransport | None = None, max_tokens: int = 400) -> Callable[[bytes, str], dict[str, Any]]:
    """A function (jpeg bytes, prompt) -> {"tags", "objects", "caption"} backed by the local Ollama server."""
    host = _host(settings)
    tag = model or vision_model(settings)
    client = httpx.Client(timeout=httpx.Timeout(30, read=float(settings.get("providers.llm.ollama.timeout_seconds") or 600)), transport=transport)
    try:
        r = client.get(f"{host}/api/tags")
        have = [m.get("name") or m.get("model") or "" for m in r.json().get("models") or []]
    except httpx.HTTPError as exc:
        raise ConfigError(f"Ollama is not running at {host}: the vision tagger needs it.", hint="Start Ollama (`ollama serve` or the app), then retry. Without it, tag files yourself: `python -m gta_factory footage tag <file> --tags lucia,beach`.") from exc
    if not _installed(have, tag):
        raise ConfigError(f"The vision model '{tag}' is not downloaded.", hint=f"Run `ollama pull {tag}` (free), or set footage.vision_model in config.yaml.")

    def call(image: bytes, prompt: str) -> dict[str, Any]:
        body = {
            "model": tag,
            "stream": False,
            "format": "json",
            "keep_alive": "10m",
            "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(image).decode()]}],
            "options": {"temperature": 0.1, "num_predict": max_tokens},
        }
        try:
            res = client.post(f"{host}/api/chat", json=body)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Ollama vision call failed: {exc}") from exc
        if res.status_code != 200:
            raise ProviderError(f"Ollama error {res.status_code}: {res.text[:300]}")
        text = (res.json().get("message") or {}).get("content", "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.S)
            data = json.loads(m.group(0)) if m else {}
        return data if isinstance(data, dict) else {}

    return call


def frame_jpeg(path: Path, t: float | None = None, width: int = 768) -> bytes:
    """One frame (at ``t`` seconds of a video, or a picture) as a JPEG, ``width`` pixels wide."""
    cmd = ["ffmpeg", "-v", "error"]
    if t is not None:
        cmd += ["-ss", f"{t:.2f}"]
    cmd += ["-i", str(path), "-frames:v", "1", "-vf", f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4", "-"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        raise ProviderError(f"Could not read a frame of {path.name}: {proc.stderr.decode(errors='replace')[-200:]}")
    return proc.stdout


def clean_answer(data: dict[str, Any], vocab: Vocabulary) -> dict[str, Any]:
    tags = {c for t in data.get("tags") or [] if isinstance(t, str) and (c := vocab.canonical(t)) in vocab.concepts}
    objects: list[str] = []
    for o in data.get("objects") or []:
        if not isinstance(o, str) or not o.strip():
            continue
        c = vocab.canonical(o)
        if c in vocab.concepts:  # "alligator", "police car" ... are concepts already
            tags.add(c)
            continue
        toks = phrase_tokens(o)[-2:]  # "palm tree" -> palm_tree (said "palm trees" -> shown)
        if not toks or any(ch.isdigit() for tok in toks for ch in tok) or (len(toks) == 1 and (toks[0] in GENERIC_OBJECTS or len(toks[0]) <= 2)):
            continue
        n = "_".join(toks)
        if n not in objects:
            objects.append(n)
    return {"tags": sorted(tags), "objects": objects[:6], "caption": str(data.get("caption") or "")[:200]}


def pending(settings: Any, index: dict[str, Any] | None = None, *, force: bool = False) -> list[dict[str, Any]]:
    """Shots and photos the vision model has not looked at yet."""
    index = index or build_index(settings)
    done = (read_json(autotags_path(settings), {}) or {}).get("items") or {}
    root = footage_dir(settings)
    todo = []
    for v in index.get("videos", []):
        for sh in v.get("shots", []):
            if not sh.get("ok"):
                continue
            key = shot_key(v["file"], v.get("size", 0), sh)
            if force or key not in done:
                todo.append({"key": key, "path": root / v["file"], "t": round((sh["s"] + sh["e"]) / 2, 2), "label": f"{v['file']} @ {sh['s']:.1f}s"})
    for p in index.get("photos", []):
        key = photo_key(p["file"], p.get("size", 0))
        src = still_shot_key(index, p)
        if not force and src and src in done:
            continue  # a still of a shot that is tagged already
        if force or key not in done:
            todo.append({"key": key, "path": root / p["file"], "t": None, "label": p["file"]})
    return todo


def autotag(
    settings: Any,
    *,
    force: bool = False,
    limit: int | None = None,
    model: str | None = None,
    tagger: Callable[[bytes, str], dict[str, Any]] | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Tag every untagged shot / photo. Returns {"tagged": n, "left": n, "model": ...}."""
    index = build_index(settings, log=log)
    todo = pending(settings, index, force=force)
    if limit is not None:
        todo = todo[: max(0, limit)]
    if not todo:
        log("footage: every shot and photo is already tagged")
        return {"tagged": 0, "left": 0, "model": None}
    vocab = load_vocabulary(settings)
    tag_model = model or vision_model(settings)
    call = tagger or ollama_tagger(settings, tag_model)
    prompt = PROMPT.format(labels=labels_prompt(vocab))
    path = autotags_path(settings)
    data = read_json(path, {}) or {}
    data.setdefault("items", {})
    log(f"footage: tagging {len(todo)} shot(s) / photo(s) with {tag_model if tagger is None else 'the given tagger'} (only once: results are cached)")
    done = 0
    for i, it in enumerate(todo):
        try:
            answer = clean_answer(call(frame_jpeg(it["path"], it["t"]), prompt), vocab)
        except ProviderError as exc:
            log(f"footage: {it['label']}: {exc.message}")
            continue
        data["items"][it["key"]] = {**answer, "model": tag_model if tagger is None else "custom"}
        done += 1
        if answer["tags"] or answer["objects"]:
            log(f"  {it['label']}: {', '.join(answer['tags'] + answer['objects'])}")
        if (i + 1) % 10 == 0:
            write_json(path, data)  # resumable
    write_json(path, data)
    left = len(pending(settings, index))
    log(f"footage: {done} tagged" + (f", {left} left" if left else ""))
    return {"tagged": done, "left": left, "model": tag_model}


def vision_status(settings: Any) -> tuple[bool, str]:
    """(ready, one-line status) for `doctor` and the dashboard."""
    tag = vision_model(settings)
    try:
        with httpx.Client(timeout=3) as c:
            have = [m.get("name") or "" for m in c.get(f"{_host(settings)}/api/tags").json().get("models") or []]
    except (httpx.HTTPError, ValueError):
        return False, f"Ollama not running: start it, then `ollama pull {tag}` to tag footage automatically"
    if _installed(have, tag):
        return True, f"{tag} (local, free)"
    return False, f"`ollama pull {tag}` to tag footage automatically (or tag files yourself)"
