"""What the narration talks about, in words the footage library understands.

A *concept* is something a viewer can see: a character (``lucia``), a place
(``vice_city``), a thing or an action (``police``, ``alligator``, ``beach``).
Each concept lists the words or phrases that *call for it* in the narration
(``say``), an optional visual description for the vision tagger (``look``),
file-name patterns that always carry it (``files``, e.g. a trailer's YouTube id)
and related concepts to fall back on (``also``).

Built-in concepts: ``default_tags.yaml`` next to this file. Yours:
``knowledge/footage_tags.yaml`` (``footage.tags_file``), merged on top. Every tag
found on a file of the library also becomes a concept of its own, so tagging a
screenshot ``flamingo`` is enough for any scene that *says* "flamingo" to show it.

Matching is done on normalised tokens (lower case, no punctuation, possessive
and plural removed), longest phrase first: "Vice City" is one mention of
``vice_city``, not "vice" + "city".
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

import yaml

HERE = Path(__file__).parent
_WORD = re.compile(r"\S+")
_SPLIT = re.compile(r"[\s\-/–—_]+")

# words of a file name that never describe what is in the picture
FILLER = frozenset(
    """gta grand theft auto vi vi6 rockstar rockstargames games game official trailer teaser still stills screenshot screenshots
    screen shot shots img image images photo photos pic picture video clip clips final copy edit edited hd uhd fhd 4k 8k
    the and with from new of in on at by for to jpg jpeg png webp mp4 mov mkv webm part version""".split()
)
# vision-tagger nouns too generic to call for a picture on their own
GENERIC_OBJECTS = frozenset(
    "person people man men woman women sky tree trees building buildings road street water light lights image frame scene background thing view".split()
)


def norm(tok: str) -> str:
    """``Lucia's`` -> ``lucia``, ``Cops`` -> ``cop``, ``Dre'Quan`` -> ``drequan``, ``cities`` -> ``city``."""
    t = (tok or "").lower().replace("’", "'")
    t = re.sub(r"'s$", "", t)
    t = re.sub(r"[^a-z0-9]", "", t)
    if len(t) > 4 and t.endswith("ies"):
        t = t[:-3] + "y"
    elif len(t) > 3 and t.endswith("s") and not t.endswith(("ss", "us", "is")):
        t = t[:-1]
    return t


def phrase_tokens(text: str) -> list[str]:
    return [n for part in _SPLIT.split(text or "") if (n := norm(part))]


def word_tokens(text: str) -> list[tuple[str, int]]:
    """Normalised tokens of a narration with the index of the (whitespace) word each comes from."""
    out: list[tuple[str, int]] = []
    for i, w in enumerate(_WORD.findall(text or "")):
        for part in re.split(r"[\-/–—_]+", w):
            if n := norm(part):
                out.append((n, i))
    return out


def concept_id(text: str) -> str:
    """Identifier of a concept as written in a tags file: ``Leonida Keys`` -> ``leonida_keys``."""
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower().replace("'", "").replace("’", "")).strip("_")


class Vocabulary:
    def __init__(self, concepts: dict[str, dict[str, Any]]):
        self.concepts = concepts
        self._aliases: dict[tuple[str, ...], list[str]] = {}
        for cid, c in concepts.items():
            for alias in [cid.replace("_", " "), *(c.get("say") or [])]:
                key = tuple(phrase_tokens(str(alias)))
                if key and cid not in self._aliases.setdefault(key, []):
                    self._aliases[key].append(cid)
        self._max_len = max((len(k) for k in self._aliases), default=1)
        self._file_patterns = [
            (cid, re.compile(r"(?<![a-z0-9])" + re.escape(_flat(str(p))) + r"(?![a-z0-9])"))
            for cid, c in concepts.items()
            for p in c.get("files") or []
            if _flat(str(p))
        ]

    # ------------------------------------------------------------------ lookups
    def canonical(self, tag: str) -> str:
        """``Vice City`` -> ``vice_city``, ``Lucia Caminos`` -> ``lucia``, ``pink cars`` -> ``pink_car``."""
        key = tuple(phrase_tokens(tag))
        if not key:
            return ""
        hit = self._aliases.get(key)
        return hit[0] if hit else "_".join(key)

    def also(self, cid: str) -> list[str]:
        return [c for a in (self.concepts.get(cid) or {}).get("also") or [] if (c := self.canonical(str(a))) and c != cid]

    def look(self, cid: str) -> str:
        return str((self.concepts.get(cid) or {}).get("look") or "")

    def with_tags(self, tags: Iterable[str]) -> "Vocabulary":
        """A copy that also knows the library's own tags as concepts."""
        extra = {t: {"say": [t.replace("_", " ")]} for t in tags if t and t not in self.concepts}
        return Vocabulary({**self.concepts, **extra}) if extra else self

    def mentions(self, text: str) -> list[dict[str, Any]]:
        """Every concept the text names, in order: [{"concepts": [...], "word": index, "said": "Vice City"}]."""
        toks = word_tokens(text)
        words = _WORD.findall(text or "")
        out: list[dict[str, Any]] = []
        i = 0
        while i < len(toks):
            for n in range(min(self._max_len, len(toks) - i), 0, -1):
                hit = self._aliases.get(tuple(t for t, _ in toks[i : i + n]))
                if hit:
                    a, b = toks[i][1], toks[i + n - 1][1]
                    said = " ".join(words[a : b + 1]).strip(" .,;:!?\"'()[]")
                    out.append({"concepts": list(hit), "word": a, "said": said})
                    i += n
                    break
            else:
                i += 1
        return out

    def tags_from_text(self, text: str) -> set[str]:
        """Tags for a file name or free text: known concepts it names, plus its other meaningful words."""
        toks = word_tokens(text)
        tags: set[str] = set()
        used: set[int] = set()
        i = 0
        while i < len(toks):
            for n in range(min(self._max_len, len(toks) - i), 0, -1):
                hit = self._aliases.get(tuple(t for t, _ in toks[i : i + n]))
                if hit and not all(t in FILLER for t, _ in toks[i : i + n]):
                    tags.add(hit[0])
                    used.update(range(i, i + n))
                    i += n
                    break
            else:
                i += 1
        for k, (t, _) in enumerate(toks):
            if k not in used and t not in FILLER and len(t) > 2 and not any(ch.isdigit() for ch in t):
                tags.add(t)
        return tags

    def file_concepts(self, name: str) -> set[str]:
        flat = _flat(re.sub(r"\.[a-z0-9]{2,4}$", "", name.lower()))
        return {cid for cid, rx in self._file_patterns if rx.search(flat)}


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_\-.\[\]()]+", " ", text.lower())).strip()


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def tags_file(settings: Any) -> Path:
    p = Path(str(settings.get("footage.tags_file", "knowledge/footage_tags.yaml")))
    return p if p.is_absolute() else settings.root / p


def load_vocabulary(settings: Any) -> Vocabulary:
    """Built-in concepts merged with ``knowledge/footage_tags.yaml`` (new concepts, extra words, or ``enabled: false``)."""
    concepts: dict[str, dict[str, Any]] = {}
    # another game's library (games.py) adds its own concepts and switches GTA 6 ones off
    extra = [Path(str(x)) for x in settings.get("footage.extra_tags_files") or []]
    extra = [x if x.is_absolute() else settings.root / x for x in extra]
    for source in (_load_yaml(HERE / "default_tags.yaml"), _load_yaml(tags_file(settings)), *(_load_yaml(x) for x in extra)):
        for raw_id, c in source.items():
            cid = concept_id(str(raw_id))
            if not cid:
                continue
            if isinstance(c, list):  # short form:  flamingo: [flamingo, pink bird]
                c = {"say": c}
            if not isinstance(c, dict):
                c = {}
            if c.get("enabled") is False:
                concepts.pop(cid, None)
                continue
            base = concepts.get(cid, {})
            merged = {**base, **{k: v for k, v in c.items() if k not in ("say", "files", "also")}}
            for key in ("say", "files", "also"):
                merged[key] = list(dict.fromkeys([*(base.get(key) or []), *[str(x) for x in c.get(key) or []]]))
            concepts[cid] = merged
    return Vocabulary(concepts)
