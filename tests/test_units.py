"""Veritabanı gerektirmeyen birim testleri.

Çalıştırma (konteyner içinde):
    docker compose exec -T factory-api python3 -m pytest -q /srv/tests
"""
import os

os.environ.setdefault("SESSION_SECRET", "x" * 40)

from app import pipeline  # noqa: E402
from app.providers import base  # noqa: E402


def test_normalize_folds_turkish_and_order():
    assert pipeline.normalize("Bal neden BOZULMAZ?") == pipeline.normalize("bozulmaz neden bal")
    assert pipeline.normalize("Işık ğüşçö") == "gusco isik"


def test_chunks_respect_limits():
    text = "Arkeologlar Mısır mezarlarında binlerce yıllık bal kavanozları buldu. Ve bu ballar bozulmamıştı."
    parts = pipeline._chunks(text)
    assert all(len(p.split()) <= 5 and len(p) <= 34 for p in parts)
    assert " ".join(parts) == text


def test_srt_timing_monotonic_and_bounded():
    scenes = [{"text": "Bir iki üç dört beş altı yedi."}, {"text": "Sekiz dokuz on."}]
    tl = [{"scene": 0, "start": 0.0, "voice": 2.0, "duration": 2.3},
          {"scene": 1, "start": 2.3, "voice": 1.0, "duration": 1.5}]
    srt = pipeline.build_srt(scenes, tl)
    cues = [b.split("\n")[1] for b in srt.strip().split("\n\n")]

    def sec(t):
        h, m, s = t.replace(",", ".").split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    times = [tuple(map(sec, c.split(" --> "))) for c in cues]
    assert all(a < b for a, b in times)
    assert all(times[i][1] <= times[i + 1][0] + 1e-6 for i in range(len(times) - 1))
    assert times[-1][1] <= 3.8


def test_fmt():
    assert pipeline._fmt(3661.5) == "01:01:01,500"


def test_registry_has_all_kinds():
    av = base.available()
    assert {"anthropic", "openai", "gemini"} <= set(av["llm"])
    assert {"openai", "fal", "local_textcard"} <= set(av["image"])
    assert {"elevenlabs", "openai", "google", "espeak"} <= set(av["tts"])
    assert "fal_video" in av["video"]
    assert base.get("video", "none") is None


def test_missing_keys_raise_not_fake_success(tmp_path, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FAL_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    import pytest
    with pytest.raises(base.ProviderNotConfigured):
        base.get("llm", "anthropic").generate_json("s", "p", {})
    with pytest.raises(base.ProviderNotConfigured):
        base.get("image", "openai").generate("p", tmp_path / "x.png")
    with pytest.raises(base.ProviderNotConfigured):
        base.get("tts", "elevenlabs").synthesize("merhaba", tmp_path / "x.mp3", "tr")


def test_youtube_metadata_private_and_disclosure():
    v = {"title": "Başlık", "description": "Açıklama", "tags": ["a"], "sources": [{"title": "S", "url": "https://x"}],
         "contains_synthetic_media": True, "language": "tr"}
    m = pipeline.youtube_metadata(v)
    assert m["status"]["privacyStatus"] == "private"
    assert m["status"]["containsSyntheticMedia"] is True
    assert m["status"]["selfDeclaredMadeForKids"] is False
    assert "yapay zekâ" in m["snippet"]["description"]


def test_image_dhash_detects_duplicates_not_different(tmp_path):
    from PIL import Image, ImageDraw
    a = Image.new("RGB", (540, 960), (30, 60, 120))
    ImageDraw.Draw(a).ellipse((100, 200, 440, 700), fill=(240, 200, 40))
    b = Image.new("RGB", (540, 960), (30, 60, 120))
    ImageDraw.Draw(b).rectangle((0, 600, 540, 960), fill=(20, 140, 60))
    a.save(tmp_path / "a.png")
    a.resize((1080, 1920)).save(tmp_path / "a_big.png")   # aynı görsel, farklı boyut
    b.save(tmp_path / "b.png")
    ha, hab, hb = (pipeline.image_dhash(tmp_path / n) for n in ("a.png", "a_big.png", "b.png"))
    dist = lambda x, y: sum(c1 != c2 for c1, c2 in zip(x, y))
    assert dist(ha, hab) <= 10
    assert dist(ha, hb) > 10
