"""Sağlayıcı arayüzleri ve kayıt defteri.

Yeni bir sağlayıcı eklemek için ilgili modülde sınıfı yazıp `register()` ile
kaydetmek yeterlidir; pipeline yalnızca arayüzü bilir. Aktif sağlayıcı
`settings.providers` ayarından seçilir, sistemin geri kalanı değişmez.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import httpx


class ProviderNotConfigured(Exception):
    """Gerekli API anahtarı / ayar yok. Sahte sonuç üretilmez; adım durur."""


class ProviderError(Exception):
    """Sağlayıcı çağrısı başarısız oldu (tekrar denenebilir olabilir)."""

    def __init__(self, msg: str, retryable: bool = True):
        super().__init__(msg)
        self.retryable = retryable


@dataclass
class LicenseInfo:
    kind: str                 # ai_generated | own_work | licensed | public_domain | cc
    provider: str
    license_name: str
    source_url: str | None = None


@dataclass
class LLMResult:
    data: dict
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model: str


@dataclass
class MediaResult:
    status: str               # ready | processing
    cost_usd: float
    license: LicenseInfo
    file_path: Path | None = None
    job_id: str | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class TTSResult:
    file_path: Path
    cost_usd: float
    chars: int
    license: LicenseInfo


class LLMProvider:
    name = "base"
    estimated_cost = 0.05

    def generate_json(self, system: str, prompt: str, schema: dict) -> LLMResult:
        raise NotImplementedError


class ImageProvider:
    name = "base"
    estimated_cost = 0.05
    is_ai = True

    def generate(self, prompt: str, out_path: Path, scene_text: str = "") -> MediaResult:
        raise NotImplementedError


class VideoProvider:
    """Asenkron video üretimi: submit -> poll -> (ready ise) indirilmiş dosya."""
    name = "base"
    estimated_cost = 0.5

    def submit(self, prompt: str, image_path: Path | None, duration: float) -> MediaResult:
        raise NotImplementedError

    def poll(self, job_id: str, out_path: Path) -> MediaResult:
        raise NotImplementedError


class TTSProvider:
    name = "base"

    def estimate(self, text: str) -> float:
        return 0.0

    def synthesize(self, text: str, out_path: Path, language: str) -> TTSResult:
        raise NotImplementedError


_REGISTRY: dict[str, dict[str, type]] = {"llm": {}, "image": {}, "video": {}, "tts": {}}


def register(kind: str):
    def deco(cls):
        _REGISTRY[kind][cls.name] = cls
        return cls
    return deco


def get(kind: str, name: str):
    if name in (None, "", "none"):
        return None
    if name not in _REGISTRY[kind]:
        raise ProviderNotConfigured(f"Bilinmeyen {kind} sağlayıcısı: {name}. "
                                    f"Mevcut: {sorted(_REGISTRY[kind])}")
    return _REGISTRY[kind][name]()


def available() -> dict[str, list[str]]:
    return {k: sorted(v) for k, v in _REGISTRY.items()}


def http() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(120, connect=15), follow_redirects=True)


def raise_for(resp: httpx.Response, provider: str) -> None:
    if resp.status_code < 400:
        return
    retryable = resp.status_code in (408, 409, 425, 429) or resp.status_code >= 500
    raise ProviderError(f"{provider} HTTP {resp.status_code}: {resp.text[:400]}", retryable=retryable)


def download(url: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with http() as c, c.stream("GET", url) as r:
        raise_for(r, "download")
        with open(out_path, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
