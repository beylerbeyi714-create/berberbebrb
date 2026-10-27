"""Ortam değişkenlerinden okunan yapılandırma. Gizli değerler asla loglanmaz."""
import json
import os
from pathlib import Path


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


DATABASE_URL = env("FACTORY_DATABASE_URL", "postgresql://factory:factory@localhost:5432/factory")
MEDIA_ROOT = Path(env("MEDIA_ROOT", "/data")).resolve()
API_TOKEN = env("FACTORY_API_TOKEN")              # n8n -> factory-api
WORKER_URL = env("WORKER_URL", "http://video-worker:8100")
WORKER_API_TOKEN = env("WORKER_API_TOKEN")
PANEL_USER = env("PANEL_USER", "admin")
PANEL_PASSWORD_HASH = env("PANEL_PASSWORD_HASH")  # bcrypt; scripts/hash_password.py ile üretilir
SESSION_SECRET = env("SESSION_SECRET")
N8N_PUBLIC_URL = env("N8N_PUBLIC_URL", "http://localhost:5678")
COOKIE_SECURE = env("COOKIE_SECURE", "true").lower() == "true"

# Sağlayıcı anahtarları — yalnızca varlıkları raporlanır, değerleri asla.
SECRET_KEYS = [
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "FAL_KEY",
    "ELEVENLABS_API_KEY", "GOOGLE_TTS_API_KEY",
]

ANTHROPIC_MODEL = env("ANTHROPIC_MODEL", "claude-opus-5-5")
ANTHROPIC_EFFORT = env("ANTHROPIC_EFFORT", "medium")
OPENAI_TEXT_MODEL = env("OPENAI_TEXT_MODEL", "gpt-5-mini")
OPENAI_IMAGE_MODEL = env("OPENAI_IMAGE_MODEL", "gpt-image-1")
OPENAI_TTS_MODEL = env("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_TTS_VOICE = env("OPENAI_TTS_VOICE", "alloy")
GEMINI_TEXT_MODEL = env("GEMINI_TEXT_MODEL", "gemini-2.5-flash")
FAL_IMAGE_MODEL = env("FAL_IMAGE_MODEL", "fal-ai/flux/schnell")
FAL_VIDEO_MODEL = env("FAL_VIDEO_MODEL", "fal-ai/kling-video/v2.1/standard/image-to-video")
ELEVENLABS_VOICE_ID = env("ELEVENLABS_VOICE_ID")
ELEVENLABS_MODEL = env("ELEVENLABS_MODEL", "eleven_multilingual_v2")
GOOGLE_TTS_VOICE = env("GOOGLE_TTS_VOICE", "tr-TR-Wavenet-E")
ESPEAK_VOICE = env("ESPEAK_VOICE", "tr")

# Tahmini birim fiyatlar (USD). Gerçek fatura sağlayıcı panelinden doğrulanmalıdır.
# PRICING_JSON ortam değişkeniyle tamamen veya kısmen ezilebilir.
PRICING = {
    "anthropic": {"input_per_mtok": 4.00, "output_per_mtok": 20.00},      # claude-opus-5-5
    "openai_text": {"input_per_mtok": 0.25, "output_per_mtok": 2.00},
    "gemini_text": {"input_per_mtok": 0.30, "output_per_mtok": 2.50},
    "openai_image": {"per_image": 0.063},                                  # 1024x1536 medium
    "fal_image": {"per_image": 0.006},                                     # flux schnell ~2MP
    "fal_video": {"per_second": 0.056},                                    # kling standard
    "elevenlabs": {"per_char": 0.00022},
    "openai_tts": {"per_char": 0.000015},
    "google_tts": {"per_char": 0.000016},                                  # WaveNet
    "espeak": {"per_char": 0.0},
    "local_textcard": {"per_image": 0.0},
    "youtube": {"per_upload_units": 100},
}
if env("PRICING_JSON"):
    for k, v in json.loads(env("PRICING_JSON")).items():
        PRICING.setdefault(k, {}).update(v)


def secret_status() -> dict[str, bool]:
    return {k: bool(os.environ.get(k)) for k in SECRET_KEYS}
