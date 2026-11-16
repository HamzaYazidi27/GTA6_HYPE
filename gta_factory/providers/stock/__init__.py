"""Licensed stock photo adapters (Pexels, Pixabay) for atmosphere b-roll.

Both services offer free API keys and licences that allow commercial use in
videos without attribution; the factory still credits the photographer on
screen and in the description. Adapters are only active when their key is in
``.env``. Results are photos (not game footage) and are always presented as
atmosphere, never as GTA 6 imagery.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

from ...app.core.errors import ProviderError
from ...app.core.utils import slugify

UA = {"User-Agent": "GTA6VideoFactory/1.0 (stock search)"}


@dataclass
class StockPhoto:
    provider: str
    photo_id: str
    page_url: str
    download_url: str
    width: int
    height: int
    author: str
    license: str
    license_url: str
    commercial_use: bool = True
    attribution_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StockProvider:
    name = "base"
    env_key = ""

    def available(self) -> bool:
        return bool(os.environ.get(self.env_key))

    def search(self, query: str, *, vertical: bool = False, limit: int = 6) -> list[StockPhoto]:  # pragma: no cover - interface
        raise NotImplementedError


class PexelsProvider(StockProvider):
    name = "pexels"
    env_key = "PEXELS_API_KEY"

    def search(self, query: str, *, vertical: bool = False, limit: int = 6) -> list[StockPhoto]:
        try:
            r = httpx.get(
                "https://api.pexels.com/v1/search",
                params={"query": query, "per_page": limit, "orientation": "portrait" if vertical else "landscape"},
                headers={**UA, "Authorization": os.environ[self.env_key]},
                timeout=30,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"Pexels search failed: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise ProviderError(f"Pexels answered HTTP {r.status_code}", hint="Check PEXELS_API_KEY in .env.")
        out = []
        for p in r.json().get("photos", []):
            src = p.get("src") or {}
            out.append(
                StockPhoto("pexels", str(p["id"]), p.get("url", ""), src.get("large2x") or src.get("original", ""), int(p.get("width") or 0), int(p.get("height") or 0), p.get("photographer") or "", "Pexels License", "https://www.pexels.com/license/")
            )
        return out


class PixabayProvider(StockProvider):
    name = "pixabay"
    env_key = "PIXABAY_API_KEY"

    def search(self, query: str, *, vertical: bool = False, limit: int = 6) -> list[StockPhoto]:
        try:
            r = httpx.get(
                "https://pixabay.com/api/",
                params={"key": os.environ[self.env_key], "q": query, "image_type": "photo", "orientation": "vertical" if vertical else "horizontal", "safesearch": "true", "per_page": max(3, limit)},
                headers=UA,
                timeout=30,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"Pixabay search failed: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise ProviderError(f"Pixabay answered HTTP {r.status_code}", hint="Check PIXABAY_API_KEY in .env.")
        out = []
        for h in r.json().get("hits", [])[:limit]:
            out.append(
                StockPhoto("pixabay", str(h["id"]), h.get("pageURL", ""), h.get("largeImageURL", ""), int(h.get("imageWidth") or 0), int(h.get("imageHeight") or 0), h.get("user") or "", "Pixabay Content License", "https://pixabay.com/service/license-summary/")
            )
        return out


REGISTRY: dict[str, type[StockProvider]] = {"pexels": PexelsProvider, "pixabay": PixabayProvider}


def get_stock_providers(settings: Any) -> list[StockProvider]:
    out = []
    for name, cls in REGISTRY.items():
        if (settings.get(f"providers.stock.{name}") or {}).get("enabled", True):
            p = cls()
            if p.available():
                out.append(p)
    return out


def download_photo(photo: StockPhoto, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = ".png" if photo.download_url.lower().split("?")[0].endswith(".png") else ".jpg"
    path = dest_dir / f"{photo.provider}_{slugify(photo.photo_id)}{ext}"
    if path.exists() and path.stat().st_size > 0:
        return path
    try:
        r = httpx.get(photo.download_url, headers=UA, timeout=60, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise ProviderError(f"Could not download {photo.provider} photo {photo.photo_id}: {type(exc).__name__}") from exc
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
        raise ProviderError(f"{photo.provider} photo {photo.photo_id} download failed (HTTP {r.status_code}).")
    path.write_bytes(r.content)
    return path
