"""Metinden sese sağlayıcıları: ElevenLabs, OpenAI, Google Cloud TTS, espeak-ng (yerel)."""
from __future__ import annotations

import base64
import os
import subprocess

from .. import config
from .base import LicenseInfo, ProviderError, ProviderNotConfigured, TTSProvider, TTSResult, http, raise_for, register


@register("tts")
class ElevenLabsTTS(TTSProvider):
    name = "elevenlabs"

    def estimate(self, text):
        return len(text) * config.PRICING["elevenlabs"]["per_char"]

    def synthesize(self, text, out_path, language):
        key = os.environ.get("ELEVENLABS_API_KEY")
        if not key or not config.ELEVENLABS_VOICE_ID:
            raise ProviderNotConfigured("ELEVENLABS_API_KEY veya ELEVENLABS_VOICE_ID tanımlı değil")
        with http() as c:
            r = c.post(f"https://api.elevenlabs.io/v1/text-to-speech/{config.ELEVENLABS_VOICE_ID}",
                       params={"output_format": "mp3_44100_128"}, headers={"xi-api-key": key},
                       json={"text": text, "model_id": config.ELEVENLABS_MODEL, "language_code": language})
        raise_for(r, "elevenlabs")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(r.content)
        return TTSResult(out_path, self.estimate(text), len(text),
                         LicenseInfo("ai_generated", "elevenlabs", "ElevenLabs Terms — commercial use per plan"))


@register("tts")
class OpenAITTS(TTSProvider):
    name = "openai"

    def estimate(self, text):
        return len(text) * config.PRICING["openai_tts"]["per_char"]

    def synthesize(self, text, out_path, language):
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ProviderNotConfigured("OPENAI_API_KEY tanımlı değil")
        with http() as c:
            r = c.post("https://api.openai.com/v1/audio/speech", headers={"Authorization": f"Bearer {key}"},
                       json={"model": config.OPENAI_TTS_MODEL, "voice": config.OPENAI_TTS_VOICE,
                             "input": text, "response_format": "mp3"})
        raise_for(r, "openai_tts")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(r.content)
        # OpenAI politikası: kullanıcıya sesin yapay zekâ ile üretildiği açıklanmalıdır.
        return TTSResult(out_path, self.estimate(text), len(text),
                         LicenseInfo("ai_generated", "openai", "OpenAI Terms — AI voice disclosure required"))


@register("tts")
class GoogleTTS(TTSProvider):
    name = "google"

    def estimate(self, text):
        return len(text) * config.PRICING["google_tts"]["per_char"]

    def synthesize(self, text, out_path, language):
        key = os.environ.get("GOOGLE_TTS_API_KEY")
        if not key:
            raise ProviderNotConfigured("GOOGLE_TTS_API_KEY tanımlı değil")
        lang_code = {"tr": "tr-TR", "en": "en-US"}.get(language, language)
        with http() as c:
            r = c.post("https://texttospeech.googleapis.com/v1/text:synthesize",
                       headers={"x-goog-api-key": key},
                       json={"input": {"text": text},
                             "voice": {"languageCode": lang_code, "name": config.GOOGLE_TTS_VOICE},
                             "audioConfig": {"audioEncoding": "MP3", "speakingRate": 1.08}})
        raise_for(r, "google_tts")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(base64.b64decode(r.json()["audioContent"]))
        return TTSResult(out_path, self.estimate(text), len(text),
                         LicenseInfo("ai_generated", "google", "Google Cloud Terms"))


@register("tts")
class EspeakTTS(TTSProvider):
    """Ücretsiz, yerel, robotik ses. Yalnızca test ve geliştirme içindir."""
    name = "espeak"

    def synthesize(self, text, out_path, language):
        voice = config.ESPEAK_VOICE if language == "tr" else language
        out_path.parent.mkdir(parents=True, exist_ok=True)
        wav = out_path.with_suffix(".wav")
        p = subprocess.run(["espeak-ng", "-v", voice, "-s", "165", "-w", str(wav), text],
                           capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            raise ProviderError(f"espeak-ng: {p.stderr[:300]}", retryable=False)
        if out_path.suffix != ".wav":
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-ar", "48000", str(out_path)],
                           check=True, timeout=60)
            wav.unlink(missing_ok=True)
        return TTSResult(out_path, 0.0, len(text),
                         LicenseInfo("own_work", "espeak-ng", "espeak-ng (GPLv3 yazılım; çıktı kullanıcıya ait)"))
