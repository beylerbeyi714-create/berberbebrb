"""Görsel ve video üretim sağlayıcıları.

- openai          : gpt-image-1 (senkron)
- fal             : FLUX görseli (fal kuyruk API'si)
- fal_video       : Kling image-to-video (asenkron: submit/poll)
- local_textcard  : YAPAY ZEKÂ DEĞİLDİR. Yerel PIL ile metin kartı üretir; yalnızca
                    API anahtarı olmadan zinciri test etmek içindir. QC bunu uyarı
                    olarak işaretler ve bu varlıklarla üretilen videolar herkese açık
                    yayına uygun sayılmaz.
"""
from __future__ import annotations

import base64
import hashlib
import os
import textwrap
import time
from pathlib import Path

from .. import config
from .base import (ImageProvider, LicenseInfo, MediaResult, ProviderError, ProviderNotConfigured,
                   VideoProvider, download, http, raise_for, register)

SHORTS_STYLE = ("vertical 9:16 composition, cinematic lighting, high detail, no text, "
                "no watermark, no logos, no real identifiable people")


@register("image")
class OpenAIImage(ImageProvider):
    name = "openai"

    @property
    def estimated_cost(self):
        return config.PRICING["openai_image"]["per_image"]

    def generate(self, prompt, out_path, scene_text=""):
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ProviderNotConfigured("OPENAI_API_KEY tanımlı değil")
        with http() as c:
            r = c.post("https://api.openai.com/v1/images/generations",
                       headers={"Authorization": f"Bearer {key}"},
                       json={"model": config.OPENAI_IMAGE_MODEL, "prompt": f"{prompt}. {SHORTS_STYLE}",
                             "size": "1024x1536", "quality": "medium", "n": 1})
        raise_for(r, "openai_image")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(base64.b64decode(r.json()["data"][0]["b64_json"]))
        return MediaResult("ready", self.estimated_cost,
                           LicenseInfo("ai_generated", "openai", "OpenAI Terms of Use — output assigned to user"),
                           file_path=out_path)


def _fal_queue_submit(model: str, payload: dict) -> dict:
    key = os.environ.get("FAL_KEY")
    if not key:
        raise ProviderNotConfigured("FAL_KEY tanımlı değil")
    with http() as c:
        r = c.post(f"https://queue.fal.run/{model}", headers={"Authorization": f"Key {key}"}, json=payload)
    raise_for(r, "fal")
    return r.json()


def _fal_get(url: str) -> dict:
    with http() as c:
        r = c.get(url, headers={"Authorization": f"Key {os.environ.get('FAL_KEY', '')}"})
    raise_for(r, "fal")
    return r.json()


def _fal_job_id(sub: dict) -> str:
    # status_url ve response_url'i tek bir job_id içinde saklarız
    return f"{sub['status_url']}|{sub['response_url']}"


@register("image")
class FalImage(ImageProvider):
    name = "fal"

    @property
    def estimated_cost(self):
        return config.PRICING["fal_image"]["per_image"]

    def generate(self, prompt, out_path, scene_text=""):
        sub = _fal_queue_submit(config.FAL_IMAGE_MODEL, {
            "prompt": f"{prompt}. {SHORTS_STYLE}", "image_size": "portrait_16_9", "num_images": 1})
        status_url, response_url = _fal_job_id(sub).split("|")
        for _ in range(60):                 # görsel için kısa senkron bekleme (en fazla ~2 dk)
            st = _fal_get(status_url)
            if st.get("status") == "COMPLETED":
                break
            time.sleep(2)
        else:
            raise ProviderError("fal image zaman aşımı", retryable=True)
        res = _fal_get(response_url)
        download(res["images"][0]["url"], out_path)
        return MediaResult("ready", self.estimated_cost,
                           LicenseInfo("ai_generated", "fal", f"fal.ai Terms + {config.FAL_IMAGE_MODEL} model license"),
                           file_path=out_path, job_id=sub.get("request_id"))


@register("video")
class FalVideo(VideoProvider):
    name = "fal_video"

    @property
    def estimated_cost(self):
        return config.PRICING["fal_video"]["per_second"] * 5

    def submit(self, prompt, image_path, duration):
        if image_path is None:
            raise ProviderError("fal_video image-to-video için sahne görseli gerekli", retryable=False)
        data_uri = "data:image/png;base64," + base64.b64encode(image_path.read_bytes()).decode()
        sub = _fal_queue_submit(config.FAL_VIDEO_MODEL, {
            "prompt": prompt, "image_url": data_uri, "duration": "5" if duration <= 5 else "10",
            "aspect_ratio": "9:16"})
        secs = 5 if duration <= 5 else 10
        return MediaResult("processing", config.PRICING["fal_video"]["per_second"] * secs,
                           LicenseInfo("ai_generated", "fal", f"fal.ai Terms + {config.FAL_VIDEO_MODEL}"),
                           job_id=_fal_job_id(sub))

    def poll(self, job_id, out_path):
        status_url, response_url = job_id.split("|")
        st = _fal_get(status_url)
        if st.get("status") != "COMPLETED":
            return MediaResult("processing", 0, LicenseInfo("ai_generated", "fal", ""), job_id=job_id,
                               meta={"provider_status": st.get("status")})
        res = _fal_get(response_url)
        download(res["video"]["url"], out_path)
        return MediaResult("ready", 0, LicenseInfo("ai_generated", "fal", f"fal.ai Terms + {config.FAL_VIDEO_MODEL}"),
                           file_path=out_path, job_id=job_id)


@register("image")
class LocalTextCard(ImageProvider):
    name = "local_textcard"
    estimated_cost = 0.0
    is_ai = False

    def generate(self, prompt, out_path, scene_text=""):
        from PIL import Image, ImageDraw, ImageFont

        w, h = 1080, 1920
        seed = int(hashlib.sha256((prompt + scene_text).encode()).hexdigest()[:6], 16)
        c1 = ((seed >> 16) % 120 + 20, (seed >> 8) % 120 + 20, seed % 120 + 60)
        c2 = (c1[2] // 3, c1[0] // 3, c1[1] // 2)
        img = Image.new("RGB", (w, h))
        px = img.load()
        for y in range(h):              # dikey gradyan arka plan
            t = y / h
            row = tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3))
            for x in range(w):
                px[x, y] = row
        d = ImageDraw.Draw(img)
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        big = ImageFont.truetype(font_path, 64)
        small = ImageFont.truetype(font_path, 34)
        # Anlatım metni altyazıda zaten var; kart, üretilecek görselin talimatını gösterir.
        d.text((60, 260), "GÖRSEL TALİMATI", font=small, fill=(255, 220, 120))
        y = 320
        for ln in textwrap.wrap(prompt, 24)[:10]:
            d.text((60, y), ln, font=big, fill=(255, 255, 255))
            y += 84
        d.text((40, h - 80), "TEST GÖRSELİ — yapay zekâ üretimi değil", font=small, fill=(255, 220, 120))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, "PNG")
        return MediaResult("ready", 0.0, LicenseInfo("own_work", "local_textcard",
                                                     "Yerel üretim — sistem sahibine ait"), file_path=out_path)
