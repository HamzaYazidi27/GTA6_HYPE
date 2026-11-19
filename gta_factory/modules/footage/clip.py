"""CLIP embeddings (free, local, ONNX): find the frame of the footage that shows what a sentence says.

The model is OpenAI's CLIP ViT-B/32 in the ONNX export published by Xenova on Hugging Face
(quantized: ~150 MB, CPU only, no PyTorch). Text and pictures land in one 512-d space, so a
narrated phrase ("race jet skis") can be compared with every indexed frame of the library.

Files (``footage.clip.dir``, default ``assets/models/clip``): ``text_model_quantized.onnx``,
``vision_model_quantized.onnx``, ``tokenizer.json``; ``ensure_clip`` downloads them once.

The frame index (``index_frames``) is cached next to the footage library
(``clip_index.npz`` + ``clip_index.json``): one row per sampled frame of every usable shot
(about two frames per second) and per photo; only new or changed footage is embedded again.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ...app.core.errors import ConfigError

REPO = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/"
FILES = {"text_model_quantized.onnx": "onnx/text_model_quantized.onnx", "vision_model_quantized.onnx": "onnx/vision_model_quantized.onnx", "tokenizer.json": "tokenizer.json"}
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
SIZE = 224
INDEX_VERSION = 1


def clip_dir(settings: Any) -> Path:
    return settings.root / str(settings.get("footage.clip.dir") or "assets/models/clip")


def clip_ready(settings: Any) -> bool:
    d = clip_dir(settings)
    return all((d / f).exists() and (d / f).stat().st_size > 1000 for f in FILES)


def ensure_clip(settings: Any, *, log: Callable[[str], None] = print) -> bool:
    """Download the CLIP files once (about 150 MB). Returns False when it cannot (offline, blocked)."""
    import httpx

    d = clip_dir(settings)
    d.mkdir(parents=True, exist_ok=True)
    for name, rel in FILES.items():
        dest = d / name
        if dest.exists() and dest.stat().st_size > 1000:
            continue
        log(f"clip: downloading {name} (one time) ...")
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            with httpx.stream("GET", REPO + rel, follow_redirects=True, timeout=httpx.Timeout(60, read=300)) as r:
                r.raise_for_status()
                with tmp.open("wb") as fh:
                    for chunk in r.iter_bytes(1 << 20):
                        fh.write(chunk)
            tmp.rename(dest)
        except Exception as exc:  # the matcher falls back to tags without CLIP
            tmp.unlink(missing_ok=True)
            log(f"clip: could not download {name}: {type(exc).__name__}: {exc}")
            return False
    return True


class Clip:
    """Text and image encoders. ``Clip.load(settings)`` returns None when the files or libraries are missing."""

    _cache: dict[str, "Clip"] = {}

    def __init__(self, folder: Path):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self.text = ort.InferenceSession(str(folder / "text_model_quantized.onnx"), opts, providers=["CPUExecutionProvider"])
        self.vision = ort.InferenceSession(str(folder / "vision_model_quantized.onnx"), opts, providers=["CPUExecutionProvider"])
        self.tok = Tokenizer.from_file(str(folder / "tokenizer.json"))
        self._text_cache: dict[str, np.ndarray] = {}

    @classmethod
    def load(cls, settings: Any) -> "Clip | None":
        folder = clip_dir(settings)
        key = str(folder)
        if key in cls._cache:
            return cls._cache[key]
        if not clip_ready(settings):
            return None
        try:
            model = cls(folder)
        except ImportError:  # onnxruntime ships with the voice; tokenizers is in the [clip] extra
            return None
        cls._cache[key] = model
        return model

    def embed_text(self, texts: list[str]) -> np.ndarray:
        out = []
        for t in texts:
            if t not in self._text_cache:
                ids = self.tok.encode(t).ids[:77]
                v = self.text.run(None, {"input_ids": np.array([ids], dtype=np.int64)})[0][0]
                self._text_cache[t] = v / (np.linalg.norm(v) + 1e-9)
            out.append(self._text_cache[t])
        return np.stack(out).astype(np.float32) if out else np.zeros((0, 512), np.float32)

    def embed_images(self, images: list[np.ndarray], batch: int = 16) -> np.ndarray:
        """``images``: RGB uint8 arrays (already 224x224, see ``preprocess``)."""
        vecs = []
        for i in range(0, len(images), batch):
            x = np.stack([((im.astype(np.float32) / 255.0 - MEAN) / STD).transpose(2, 0, 1) for im in images[i : i + batch]])
            v = self.vision.run(None, {"pixel_values": x})[0]
            vecs.append(v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9))
        return np.concatenate(vecs).astype(np.float32) if vecs else np.zeros((0, 512), np.float32)


def read_frames(path: Path, times: list[float] | None, *, crop: list[int] | None = None, size: int = SIZE) -> list[np.ndarray]:
    """RGB frames (``size`` x ``size``, short side resized then centre-cropped) at ``times`` (None: a picture)."""
    from .library import crop_filter

    vf = f"{crop_filter(crop)}scale={size}:{size}:force_original_aspect_ratio=increase,crop={size}:{size}"
    frames = []
    for t in times if times is not None else [None]:
        cmd = ["ffmpeg", "-v", "error"] + (["-ss", f"{t:.3f}"] if t is not None else []) + ["-i", str(path), "-frames:v", "1", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode == 0 and len(proc.stdout) >= size * size * 3:
            frames.append(np.frombuffer(proc.stdout[: size * size * 3], dtype=np.uint8).reshape(size, size, 3))
        else:
            frames.append(None)
    return frames


def read_frames_fast(path: Path, start: float, end: float, fps: float, *, crop: list[int] | None = None, size: int = SIZE) -> tuple[list[float], list[np.ndarray]]:
    """Every 1/``fps`` s between ``start`` and ``end`` with one ffmpeg call: (times, RGB frames)."""
    from .library import crop_filter

    dur = max(0.05, end - start)
    vf = f"fps={fps},{crop_filter(crop)}scale={size}:{size}:force_original_aspect_ratio=increase,crop={size}:{size}"
    proc = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(path), "-an", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], capture_output=True)
    n = len(proc.stdout) // (size * size * 3) if proc.returncode == 0 else 0
    frames = [np.frombuffer(proc.stdout[i * size * size * 3 : (i + 1) * size * size * 3], dtype=np.uint8).reshape(size, size, 3) for i in range(n)]
    times = [round(start + (i + 0.5) / fps, 3) for i in range(n)]
    return times, frames


# ----------------------------------------------------------------------------- the frame index

def _index_paths(settings: Any) -> tuple[Path, Path]:
    from .library import footage_dir

    root = footage_dir(settings)
    return root / "clip_index.npz", root / "clip_index.json"


def load_index(settings: Any) -> tuple[list[dict[str, Any]], np.ndarray]:
    npz, meta = _index_paths(settings)
    if not npz.exists() or not meta.exists():
        return [], np.zeros((0, 512), np.float32)
    info = json.loads(meta.read_text(encoding="utf-8"))
    if info.get("version") != INDEX_VERSION:
        return [], np.zeros((0, 512), np.float32)
    vecs = np.load(npz)["vecs"]
    return info["rows"], vecs


def index_frames(settings: Any, items: dict[str, Any], *, sample_fps: float = 2.0, log: Callable[[str], None] = print, model: Clip | None = None) -> tuple[list[dict[str, Any]], np.ndarray]:
    """Embed the frames of every usable shot (``sample_fps`` per second) and every photo; cached, incremental.

    Rows: {"key": item key, "file", "kind": "shot"|"photo", "t": seconds in the file (None for photos)}.
    """
    model = model or Clip.load(settings)
    rows, vecs = load_index(settings)
    if model is None:
        return rows, vecs
    have = {(r["key"], r.get("t")) for r in rows}
    done_keys = {r["key"] for r in rows}
    want_keys = {it["key"] for it in items["shots"] + items["photos"]}
    keep = [i for i, r in enumerate(rows) if r["key"] in want_keys]  # footage that left the library leaves the index
    rows, vecs = [rows[i] for i in keep], vecs[keep] if len(keep) else np.zeros((0, 512), np.float32)
    new_rows, new_vecs = [], []
    todo = [it for it in items["shots"] + items["photos"] if it["key"] not in done_keys]
    if todo:
        log(f"clip: indexing {len(todo)} shot(s) / photo(s) of the footage library (once, cached)")
    for k, it in enumerate(todo):
        if it["kind"] == "shot":
            times, frames = read_frames_fast(Path(it["path"]), it["s"], it["e"], sample_fps, crop=it.get("crop"))
            if not frames:
                continue
            emb = model.embed_images(frames)
            for t, v in zip(times, emb):
                if (it["key"], t) not in have:
                    new_rows.append({"key": it["key"], "file": it["file"], "kind": "shot", "t": t})
                    new_vecs.append(v)
        else:
            frames = [f for f in read_frames(Path(it.get("source_path") or it["path"]), None) if f is not None]
            if frames:
                new_rows.append({"key": it["key"], "file": it["file"], "kind": "photo", "t": None})
                new_vecs.append(model.embed_images(frames)[0])
        if (k + 1) % 25 == 0:
            log(f"clip: {k + 1}/{len(todo)}")
    if new_rows:
        rows = rows + new_rows
        vecs = np.concatenate([vecs, np.stack(new_vecs)]) if len(vecs) else np.stack(new_vecs)
    if new_rows or len(keep) != len(load_index(settings)[0]):
        npz, meta = _index_paths(settings)
        np.savez_compressed(npz, vecs=vecs.astype(np.float32))
        meta.write_text(json.dumps({"version": INDEX_VERSION, "rows": rows}), encoding="utf-8")
    return rows, vecs
