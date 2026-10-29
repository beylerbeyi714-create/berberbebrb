"""Üretim zinciri: fikir -> senaryo -> medya -> ses/altyazı -> render -> QC -> yayın devri.

Her adım idempotenttir: tekrar çağrıldığında tamamlanmış işi yeniden yapmaz
ve ücretli API'yi tekrar çağırmaz. Aşama geçişleri `videos.stage` alanındadır.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import subprocess
import unicodedata
from pathlib import Path

import httpx
from psycopg.types.json import Jsonb

from . import budget, config, db, providers
from .providers import ProviderError, ProviderNotConfigured


class StepBlocked(Exception):
    """Adım dış bir eksiklik nedeniyle ilerleyemiyor (anahtar yok, bütçe doldu, onay yok...)."""


# =============================================================== yardımcılar
_TR = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).lower().translate(_TR)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return " ".join(sorted(set(t.split())))


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def file_sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def media_duration(p: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(p)], capture_output=True, text=True, timeout=30)
    return float(out.stdout.strip())


def rel(p: Path) -> str:
    return str(p.resolve().relative_to(config.MEDIA_ROOT))


def video_dir(video_id) -> Path:
    d = config.MEDIA_ROOT / "media" / str(video_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def provider_for(kind: str):
    name = (db.get_setting("providers", {}) or {}).get(kind)
    return providers.get(kind, name)


def license_id(lic: providers.LicenseInfo) -> str:
    row = db.one("SELECT id FROM licenses WHERE kind=%s AND provider=%s AND license_name=%s "
                 "AND source_url IS NOT DISTINCT FROM %s LIMIT 1",
                 (lic.kind, lic.provider, lic.license_name, lic.source_url))
    if row:
        return row["id"]
    # Yapay zekâ / kendi üretimimiz: lisans türü bilinir. Üçüncü taraf kaynaklar elle doğrulanmalıdır.
    verified = lic.kind in ("ai_generated", "own_work")
    return db.one("INSERT INTO licenses (kind, provider, license_name, source_url, verified, verified_by) "
                  "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                  (lic.kind, lic.provider, lic.license_name, lic.source_url, verified,
                   "system" if verified else None))["id"]


def set_stage(video_id, stage: str, error: str | None = None) -> None:
    db.exec_("UPDATE videos SET stage=%s, last_error=%s WHERE id=%s", (stage, error, video_id))


def get_video(video_id) -> dict:
    v = db.one("SELECT v.*, s.body, s.title, s.description, s.tags, s.sources, s.hook "
               "FROM videos v JOIN scripts s ON s.id=v.script_id WHERE v.id=%s", (video_id,))
    if not v:
        raise KeyError(f"video {video_id} bulunamadı")
    return v


def scenes_of(v: dict) -> list[dict]:
    return v["body"]["scenes"]


def max_retries() -> int:
    return int(db.get_setting("max_retries", 3))


# =============================================================== 1) içerik araştırma
IDEAS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["ideas"],
    "properties": {"ideas": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["topic", "category", "angle", "score", "why"],
        "properties": {"topic": {"type": "string"}, "category": {"type": "string"},
                       "angle": {"type": "string"}, "score": {"type": "number"},
                       "why": {"type": "string"}}}}},
}


def fetch_trends(geo: str = "TR", limit: int = 15) -> list[str]:
    """Google Trends günlük RSS başlıkları (erişilemezse boş liste; hata değildir)."""
    try:
        r = httpx.get(f"https://trends.google.com/trending/rss?geo={geo}", timeout=15)
        r.raise_for_status()
        return re.findall(r"<title>([^<]+)</title>", r.text)[1:limit + 1]
    except Exception:  # noqa: BLE001
        return []


def add_idea(topic: str, category: str | None, angle: str | None, source: str, score: float,
             language: str = "tr", meta: dict | None = None) -> dict:
    norm = normalize(topic)
    recent = db.all_("SELECT topic FROM content_ideas ORDER BY created_at DESC LIMIT 500")
    for r in recent:
        if difflib.SequenceMatcher(None, norm, normalize(r["topic"])).ratio() > 0.85:
            return {"created": False, "reason": f"benzer konu var: {r['topic']}"}
    row = db.one("INSERT INTO content_ideas (channel_id, topic, topic_hash, category, language, angle, source, "
                 "score, meta) SELECT id, %s,%s,%s,%s,%s,%s,%s,%s FROM channels WHERE is_default "
                 "ON CONFLICT (topic_hash) DO NOTHING RETURNING id",
                 (topic, sha256(norm), category, language, angle, source, score, Jsonb(meta or {})))
    return {"created": bool(row), "id": row and str(row["id"]),
            "reason": None if row else "aynı konu daha önce kaydedilmiş"}


def research(count: int = 10) -> dict:
    llm = provider_for("llm")
    if llm is None:
        raise ProviderNotConfigured("LLM sağlayıcısı seçilmemiş")
    budget.ensure_budget(llm.estimated_cost)
    cats = db.get_setting("categories", [])
    lang = db.get_setting("language", "tr")
    done = [r["topic"] for r in db.all_("SELECT topic FROM content_ideas ORDER BY created_at DESC LIMIT 80")]
    trends = fetch_trends()
    prompt = (
        f"Dil: {lang}. Kategoriler: {', '.join(cats)}.\n"
        f"{count} adet YouTube Shorts (20-45 sn) konu fikri üret. Her fikir doğrulanabilir bilgiye dayanmalı, "
        "özgün bir açı içermeli, telifli eserlere/kişilere bağımlı olmamalı ve clickbait yalan içermemeli.\n"
        f"Score: 0-100 arası tahmini ilgi + özgünlük puanı.\n"
        f"Daha önce işlenen konular (tekrarlama): {json.dumps(done, ensure_ascii=False)}\n"
        f"Güncel trend başlıkları (ilham, zorunlu değil): {json.dumps(trends, ensure_ascii=False)}")
    try:
        res = llm.generate_json("Sen kısa video içerik stratejistisin. Sadece istenen JSON'u üret.",
                                prompt, IDEAS_SCHEMA)
    except ProviderError:
        budget.record(llm.name, "research", 0, False)
        raise
    budget.record(llm.name, "research", res.cost_usd, True, units=res.input_tokens + res.output_tokens,
                  unit_type="tokens")
    out = []
    for idea in res.data["ideas"]:
        r = add_idea(idea["topic"], idea["category"], idea["angle"], f"llm:{llm.name}",
                     max(0, min(100, idea["score"])), lang, {"why": idea["why"], "trends_used": bool(trends)})
        out.append({"topic": idea["topic"], **r})
    return {"ideas": out, "trend_source_available": bool(trends), "cost_usd": res.cost_usd}


# =============================================================== 2) senaryo
SCRIPT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["hook", "hook_visual_prompt", "scenes", "title", "description", "tags", "sources"],
    "properties": {
        "hook": {"type": "string"},
        "hook_visual_prompt": {"type": "string"},
        "scenes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "visual_prompt"],
            "properties": {"text": {"type": "string"}, "visual_prompt": {"type": "string"}}}},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["title", "url"],
            "properties": {"title": {"type": "string"}, "url": {"type": "string"}}}},
    },
}
WORDS_PER_SEC = 2.4   # Türkçe anlatım hızı yaklaşık değeri


def validate_script(d: dict) -> list[str]:
    errs = []
    lo, hi = float(db.get_setting("min_duration_sec", 20)), float(db.get_setting("max_duration_sec", 45))
    words = len(" ".join([d["hook"]] + [s["text"] for s in d["scenes"]]).split())
    est = words / WORDS_PER_SEC
    if not (lo * 0.85 <= est <= hi * 1.05):
        errs.append(f"tahmini süre {est:.0f} sn ({words} kelime) {lo:.0f}-{hi:.0f} sn aralığı dışında")
    if not 2 <= len(d["scenes"]) <= 8:
        errs.append("sahne sayısı 2-8 olmalı")
    if not d["title"].strip() or len(d["title"]) > 100:
        errs.append("başlık boş veya 100 karakterden uzun")
    if len(d["hook"].split()) > 18:
        errs.append("giriş (hook) çok uzun; ilk 2-3 saniyede söylenebilmeli")
    return errs


def save_script(d: dict, idea_id, provider: str, language: str, content_mode: str = "original_ai",
                series_id=None, episode_number: int | None = None, episode_summary: str | None = None,
                synthetic: bool = True) -> dict:
    """d["scenes_full"] verilirse sahneler olduğu gibi kullanılır (seri bölümleri: kart + film kesiti + kart);
    aksi halde giriş (hook) ilk sahne olarak eklenir ve süre doğrulaması yapılır."""
    if "scenes_full" in d:
        scenes = d["scenes_full"]
    else:
        errs = validate_script(d)
        if errs:
            raise ValueError("; ".join(errs))
        scenes = [{"text": d["hook"], "visual_prompt": d["hook_visual_prompt"]}] + d["scenes"]
    full_text = " ".join(s.get("text", "") for s in scenes)
    # kesit sahneleri metinsiz olabilir: kaynak + zaman aralığı da özgünlük karmasına girer
    chash = sha256(normalize(full_text) + json.dumps([s.get("source") for s in scenes], sort_keys=True))
    with db.tx() as conn:
        dup = conn.execute("SELECT id FROM scripts WHERE content_hash=%s", (chash,)).fetchone()
        if dup:
            raise ValueError(f"aynı senaryo zaten var: {dup['id']}")
        s = conn.execute(
            "INSERT INTO scripts (idea_id, language, hook, body, full_text, content_hash, title, description, "
            "tags, sources, provider) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (idea_id, language, d["hook"], Jsonb({"scenes": scenes, "music": d.get("music")}), full_text, chash,
             d["title"].strip(),
             d["description"].strip(), d["tags"][:15], Jsonb(d["sources"]), provider)).fetchone()
        topic = d["title"]
        if idea_id:
            idea = conn.execute("UPDATE content_ideas SET status='scripted' WHERE id=%s RETURNING topic",
                                (idea_id,)).fetchone()
            topic = idea["topic"] if idea else topic
        v = conn.execute(
            "INSERT INTO videos (channel_id, script_id, topic, language, content_mode, series_id, episode_number, "
            "episode_summary, contains_synthetic_media) "
            "SELECT id, %s, %s, %s, %s, %s, %s, %s, %s FROM channels WHERE is_default RETURNING id",
            (s["id"], topic, language, content_mode, series_id, episode_number, episode_summary,
             synthetic)).fetchone()
    return {"script_id": str(s["id"]), "video_id": str(v["id"])}


def generate_script(idea_id=None) -> dict:
    llm = provider_for("llm")
    if llm is None:
        raise ProviderNotConfigured("LLM sağlayıcısı seçilmemiş")
    with db.tx() as conn:
        q = ("SELECT * FROM content_ideas WHERE id=%s" if idea_id else
             "SELECT * FROM content_ideas WHERE status='queued' ORDER BY score DESC, created_at "
             "LIMIT 1 FOR UPDATE SKIP LOCKED")
        idea = conn.execute(q, (idea_id,) if idea_id else None).fetchone()
    if not idea:
        raise StepBlocked("kuyrukta senaryo yazılacak fikir yok")
    budget.ensure_budget(llm.estimated_cost)
    lo, hi = db.get_setting("min_duration_sec", 20), db.get_setting("max_duration_sec", 45)
    prompt = (
        f"Konu: {idea['topic']}\nAçı: {idea.get('angle') or '-'}\nKategori: {idea.get('category') or '-'}\n"
        f"Dil: {idea['language']}\n\n"
        f"{lo}-{hi} saniyelik özgün bir YouTube Shorts senaryosu yaz (toplam {int(lo*WORDS_PER_SEC)}-"
        f"{int(hi*WORDS_PER_SEC)} kelime).\n"
        "- hook: ilk 2-3 saniyede merak uyandıran tek cümle (en fazla 14 kelime), yanıltıcı olmasın.\n"
        "- scenes: 3-6 sahne; her sahnenin 'text' alanı seslendirilecek metin, 'visual_prompt' alanı İngilizce, "
        "dikey kadraj için ayrıntılı görsel üretim talimatı (gerçek kişi, marka, logo, telifli karakter yok).\n"
        "- Bilgiler doğru ve doğrulanabilir olsun; 'sources' alanına güvenilir kaynak başlık+URL ekle.\n"
        "- title: en fazla 70 karakter, merak uyandıran ama dürüst. description: 2-3 cümle.\n"
        "- tags: 5-10 etiket.\nBaşka üreticilerin içeriğini kopyalama.")
    try:
        res = llm.generate_json("Sen ödüllü bir kısa video senaristisin. Sadece istenen JSON'u üret.",
                                prompt, SCRIPT_SCHEMA)
    except ProviderError:
        budget.record(llm.name, "script", 0, False)
        raise
    budget.record(llm.name, "script", res.cost_usd, True, units=res.input_tokens + res.output_tokens,
                  unit_type="tokens")
    out = save_script(res.data, idea["id"], f"llm:{llm.name}", idea["language"],
                      db.get_setting("content_mode", "original_ai"))
    db.exec_("UPDATE videos SET cost_usd=cost_usd+%s WHERE id=%s", (res.cost_usd, out["video_id"]))
    return {**out, "cost_usd": res.cost_usd}


# =============================================================== 3) medya üretimi
def _upsert_asset(video_id, kind, idx, provider, prompt) -> dict:
    return db.one("INSERT INTO media_assets (video_id, kind, scene_index, provider, prompt) "
                  "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (video_id, kind, scene_index) "
                  "DO UPDATE SET prompt=EXCLUDED.prompt RETURNING *", (video_id, kind, idx, provider, prompt))


def _asset_failure(asset_id, err: Exception, retryable: bool) -> bool:
    """Hata kaydeder; deneme hakkı bittiyse True döner."""
    row = db.one("UPDATE media_assets SET attempts=attempts+1, error=%s, "
                 "status=CASE WHEN %s OR attempts+1 >= %s THEN 'failed' ELSE 'pending' END "
                 "WHERE id=%s RETURNING status", (str(err)[:1000], not retryable, max_retries(), asset_id))
    return row["status"] == "failed"


def scene_kind(sc: dict) -> str:
    """'ai' (yapay zekâ görseli/klibi) · 'card' (seri bölüm kartı) · 'source_clip' (kaynak film kesiti)."""
    return sc.get("kind", "ai")


def _card_asset(video_id, i, sc, d: Path) -> None:
    from .cards import render_card
    a = _upsert_asset(video_id, "image", i, "title_card", sc.get("card_line", ""))
    if a["status"] == "ready":
        return
    p = render_card(sc["card_series"], int(sc["card_episode"]), sc["card_type"], sc.get("card_line", ""),
                    d / f"card_{i:02d}.png")
    db.exec_("UPDATE media_assets SET status='ready', file_path=%s, sha256=%s, license_id=%s, meta=%s, "
             "attempts=attempts+1, error=NULL WHERE id=%s",
             (rel(p), file_sha256(p), license_id(providers.LicenseInfo("own_work", "title_card",
                                                                         "Sistemde üretilmiş bölüm kartı")),
              Jsonb({"card": True}), a["id"]))


def _source_clip_asset(video_id, i, sc) -> None:
    src = db.one("SELECT * FROM sources WHERE id=%s", (sc["source"]["id"],))
    if not src:
        raise ValueError(f"kaynak bulunamadı: {sc['source']['id']}")
    a = _upsert_asset(video_id, "video_clip", i, "source", f"{src['title']} {sc['source']['start']}-{sc['source']['end']}")
    if a["status"] == "ready":
        return
    start, end = float(sc["source"]["start"]), float(sc["source"]["end"])
    db.exec_("UPDATE media_assets SET status='ready', file_path=%s, sha256=%s, duration_sec=%s, license_id=%s, "
             "meta=%s, attempts=attempts+1, error=NULL WHERE id=%s",
             (src["file_path"], sha256(f"{src['id']}:{start}:{end}"), round(end - start, 2), src["license_id"],
              Jsonb({"start": start, "end": end, "keep_audio": bool(sc.get("keep_audio", True)), "fit": "blur"}),
             a["id"]))


def generate_media(video_id) -> dict:
    v = get_video(video_id)
    if v["stage"] not in ("scripted", "media_pending"):
        return {"skipped": True, "stage": v["stage"]}
    scenes = scenes_of(v)
    needs_ai = any(scene_kind(s) == "ai" for s in scenes)
    img_p = provider_for("image") if needs_ai else None
    vid_p = provider_for("video") if needs_ai else None
    if needs_ai and img_p is None:
        raise ProviderNotConfigured("görsel sağlayıcısı seçilmemiş")
    set_stage(video_id, "media_pending")
    d = video_dir(video_id)
    report = []
    music = v["body"].get("music")
    if music:
        m = _upsert_asset(video_id, "music", 0, "music", music["path"])
        if m["status"] != "ready":
            db.exec_("UPDATE media_assets SET status='ready', file_path=%s, license_id=%s, meta=%s WHERE id=%s",
                     (music["path"], music["license_id"], Jsonb({"volume": music["volume"]}), m["id"]))
    for i, sc in enumerate(scenes):
        if scene_kind(sc) == "card":
            _card_asset(video_id, i, sc, d)
            report.append({"scene": i, "card": "ready"})
            continue
        if scene_kind(sc) == "source_clip":
            _source_clip_asset(video_id, i, sc)
            report.append({"scene": i, "source_clip": "ready"})
            continue
        a = _upsert_asset(video_id, "image", i, img_p.name, sc["visual_prompt"])
        if a["status"] == "ready":
            report.append({"scene": i, "image": "cached"})
        elif a["status"] == "failed":
            report.append({"scene": i, "image": "failed"})
            continue
        else:
            budget.ensure_budget(img_p.estimated_cost)
            try:
                r = img_p.generate(sc["visual_prompt"], d / f"scene_{i:02d}.png", sc["text"])
            except ProviderError as e:
                budget.record(img_p.name, "image", 0, False, video_id)
                dead = _asset_failure(a["id"], e, e.retryable)
                report.append({"scene": i, "image": "failed" if dead else "retry", "error": str(e)[:200]})
                continue
            budget.record(img_p.name, "image", r.cost_usd, True, video_id, 1, "image")
            db.exec_("UPDATE media_assets SET status='ready', file_path=%s, sha256=%s, cost_usd=%s, "
                     "license_id=%s, provider_job_id=%s, attempts=attempts+1, error=NULL, meta=%s WHERE id=%s",
                     (rel(r.file_path), file_sha256(r.file_path), r.cost_usd, license_id(r.license), r.job_id,
                      Jsonb({"is_ai": img_p.is_ai}), a["id"]))
            report.append({"scene": i, "image": "ready"})
        if vid_p is not None:
            report[-1]["clip"] = _submit_clip(video_id, i, sc, vid_p)
    return {**poll_media(video_id), "scenes": report}


def _submit_clip(video_id, i, sc, vid_p) -> str:
    img = db.one("SELECT * FROM media_assets WHERE video_id=%s AND kind='image' AND scene_index=%s",
                 (video_id, i))
    if not img or img["status"] != "ready":
        return "waiting_image"
    a = _upsert_asset(video_id, "video_clip", i, vid_p.name, sc["visual_prompt"])
    if a["status"] in ("ready", "processing", "failed"):
        return a["status"]
    budget.ensure_budget(vid_p.estimated_cost)
    try:
        r = vid_p.submit(sc["visual_prompt"], config.MEDIA_ROOT / img["file_path"], 5)
    except ProviderError as e:
        budget.record(vid_p.name, "video_submit", 0, False, video_id)
        return "failed" if _asset_failure(a["id"], e, e.retryable) else "retry"
    budget.record(vid_p.name, "video", r.cost_usd, True, video_id, 5, "seconds")
    db.exec_("UPDATE media_assets SET status='processing', provider_job_id=%s, cost_usd=%s, license_id=%s, "
             "attempts=attempts+1 WHERE id=%s", (r.job_id, r.cost_usd, license_id(r.license), a["id"]))
    return "processing"


def poll_media(video_id) -> dict:
    vid_p = provider_for("video")
    d = video_dir(video_id)
    for a in db.all_("SELECT * FROM media_assets WHERE video_id=%s AND kind='video_clip' AND status='processing'",
                     (video_id,)):
        try:
            r = vid_p.poll(a["provider_job_id"], d / f"clip_{a['scene_index']:02d}.mp4")
        except ProviderError as e:
            _asset_failure(a["id"], e, e.retryable)
            continue
        if r.status == "ready":
            db.exec_("UPDATE media_assets SET status='ready', file_path=%s, sha256=%s, duration_sec=%s WHERE id=%s",
                     (rel(r.file_path), file_sha256(r.file_path), media_duration(r.file_path), a["id"]))
    v = get_video(video_id)
    scenes = scenes_of(v)
    rows = db.all_("SELECT kind, scene_index, status FROM media_assets WHERE video_id=%s "
                   "AND kind IN ('image','video_clip')", (video_id,))
    st = {(r["kind"], r["scene_index"]): r["status"] for r in rows}
    stat: dict[str, int] = {}
    for r in rows:
        stat[f"{r['kind']}:{r['status']}"] = stat.get(f"{r['kind']}:{r['status']}", 0) + 1

    def scene_ready(i, sc) -> bool:
        k = scene_kind(sc)
        if k == "source_clip":
            return st.get(("video_clip", i)) == "ready"
        if k == "card" or vid_p is None:
            return st.get(("image", i)) == "ready"
        return st.get(("image", i)) == "ready" and st.get(("video_clip", i)) == "ready"

    if "failed" in st.values():
        set_stage(video_id, "failed", "medya üretimi deneme sınırını aştı")
        db.log_error("media", "medya üretimi başarısız", video_id, stat)
    elif all(scene_ready(i, sc) for i, sc in enumerate(scenes)):
        set_stage(video_id, "media_ready")
    return {"video_id": str(video_id), "stage": get_video(video_id)["stage"], "assets": stat}


# =============================================================== 4) ses + altyazı
def _fmt(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _chunks(text: str, max_words: int = 5, max_chars: int = 30) -> list[str]:
    out, cur = [], []
    for w in text.split():
        if cur and (len(cur) >= max_words or len(" ".join(cur + [w])) > max_chars):
            out.append(" ".join(cur))
            cur = []
        cur.append(w)
        if re.search(r"[.!?]$", w):
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    return out


def _parse_ts(t: str) -> float:
    h, m, rest = t.strip().replace(".", ",").split(":")
    sec, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms[:3].ljust(3, "0")) / 1000


def parse_srt(text: str) -> list[tuple[float, float, str]]:
    """SRT metnini (başlangıç, bitiş, yazı) listesine çevirir."""
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        for k, ln in enumerate(lines):
            if "-->" in ln:
                try:
                    a, b = ln.split("-->")
                    cues.append((_parse_ts(a), _parse_ts(b.split()[0]), "\n".join(lines[k + 1:]).strip()))
                except (ValueError, IndexError):
                    pass
                break
    return [c for c in cues if c[2]]


def source_cues(sc: dict, scene_start: float) -> list[tuple[float, float, str]]:
    """Film kesitine düşen Türkçe altyazıları bölüm zaman çizelgesine kaydırır."""
    sub = (sc.get("source") or {}).get("subtitles")
    if not sub:
        return []
    a, b = float(sc["source"]["start"]), float(sc["source"]["end"])
    out = []
    for t0, t1, txt in parse_srt((config.MEDIA_ROOT / sub).read_text(encoding="utf-8-sig")):
        if t1 <= a or t0 >= b:
            continue
        out.append((scene_start + max(t0, a) - a, scene_start + min(t1, b) - a, txt))
    return out


def build_srt(scenes: list[dict], timeline: list[dict]) -> str:
    """Sahne başına ölçülen ses süresine göre, karakter oranlı zamanlama."""
    raw: list[tuple[float, float, str]] = []
    for sc, tl in zip(scenes, timeline):
        parts = _chunks(sc.get("text", ""))
        total_chars = sum(len(p) for p in parts) or 1
        t = tl["start"] + 0.05
        for p in parts:
            dur = tl["voice"] * len(p) / total_chars
            raw.append((t, min(t + dur, tl["start"] + tl["duration"]), p))
            t += dur
        raw += source_cues(sc, tl["start"])
    raw.sort(key=lambda c: c[0])
    return "\n".join(f"{i}\n{_fmt(a)} --> {_fmt(b)}\n{txt}\n" for i, (a, b, txt) in enumerate(raw, 1))


def generate_voice(video_id) -> dict:
    v = get_video(video_id)
    if v["stage"] != "media_ready":
        return {"skipped": True, "stage": v["stage"]}
    d = video_dir(video_id)
    scenes = scenes_of(v)
    texts = [s.get("text", "").strip() for s in scenes]
    tts = provider_for("tts") if any(texts) else None
    if any(texts) and tts is None:
        raise ProviderNotConfigured("TTS sağlayıcısı seçilmemiş")
    if tts:
        budget.ensure_budget(sum(tts.estimate(t) for t in texts if t))
    timeline, start = [], 0.0
    for i, sc in enumerate(scenes):
        if not texts[i]:
            # seslendirmesiz sahne: film kesiti kendi süresince, diğerleri kısa bir an
            if scene_kind(sc) == "source_clip":
                dur = float(sc["source"]["end"]) - float(sc["source"]["start"])
            else:
                dur = float(sc.get("duration", 2.0))
            timeline.append({"scene": i, "start": round(start, 3), "voice": 0.0, "duration": round(dur, 3)})
            start += dur
            continue
        a = _upsert_asset(video_id, "voice", i, tts.name, sc["text"])
        if a["status"] != "ready":
            try:
                r = tts.synthesize(sc["text"], d / f"voice_{i:02d}.mp3", v["language"])
            except ProviderError as e:
                budget.record(tts.name, "tts", 0, False, video_id)
                if _asset_failure(a["id"], e, e.retryable):
                    set_stage(video_id, "failed", f"seslendirme başarısız: {e}")
                return {"video_id": str(video_id), "error": str(e)[:300]}
            dur = media_duration(r.file_path)
            budget.record(tts.name, "tts", r.cost_usd, True, video_id, r.chars, "chars")
            db.exec_("UPDATE media_assets SET status='ready', file_path=%s, sha256=%s, duration_sec=%s, cost_usd=%s, "
                     "license_id=%s, attempts=attempts+1, error=NULL WHERE id=%s",
                     (rel(r.file_path), file_sha256(r.file_path), dur, r.cost_usd, license_id(r.license), a["id"]))
            a = db.one("SELECT * FROM media_assets WHERE id=%s", (a["id"],))
        voice = float(a["duration_sec"])
        dur = max(voice + 0.3, 1.5 if scene_kind(sc) != "card" else 2.5)
        timeline.append({"scene": i, "start": round(start, 3), "voice": voice, "duration": round(dur, 3)})
        start += dur
    srt_path = d / "subtitles.srt"
    srt_path.write_text(build_srt(scenes, timeline), encoding="utf-8")
    sa = _upsert_asset(video_id, "subtitle", 0, "timeline", None)
    db.exec_("UPDATE media_assets SET status='ready', file_path=%s, meta=%s, license_id=%s WHERE id=%s",
             (rel(srt_path), Jsonb({"timeline": timeline}),
              license_id(providers.LicenseInfo("own_work", "factory", "Senaryodan üretilmiş altyazı")), sa["id"]))
    db.exec_("UPDATE videos SET stage='voiced', subtitle_path=%s, duration_sec=%s WHERE id=%s",
             (rel(srt_path), round(start, 2), video_id))
    lo, hi = float(db.get_setting("min_duration_sec", 20)), float(db.get_setting("max_duration_sec", 45))
    return {"video_id": str(video_id), "stage": "voiced", "duration_sec": round(start, 2),
            "duration_ok": lo <= start <= hi, "timeline": timeline}


# =============================================================== 5) render
def _worker(method: str, path: str, **kw) -> dict:
    r = httpx.request(method, config.WORKER_URL + path, headers={"X-API-Key": config.WORKER_API_TOKEN},
                      timeout=120, **kw)
    if r.status_code >= 400:
        raise ProviderError(f"video-worker HTTP {r.status_code}: {r.text[:400]}", retryable=r.status_code >= 500)
    return r.json()


def _timeline(video_id) -> list[dict]:
    sub = db.one("SELECT meta FROM media_assets WHERE video_id=%s AND kind='subtitle'", (video_id,))
    return sub["meta"]["timeline"]


def start_render(video_id) -> dict:
    v = get_video(video_id)
    if v["stage"] == "rendering":
        return check_render(video_id)
    if v["stage"] != "voiced":
        return {"skipped": True, "stage": v["stage"]}
    assets = {(a["kind"], a["scene_index"]): a for a in
              db.all_("SELECT * FROM media_assets WHERE video_id=%s AND status='ready'", (video_id,))}
    scenes = []
    for tl in _timeline(video_id):
        i = tl["scene"]
        clip, img, voice = assets.get(("video_clip", i)), assets.get(("image", i)), assets.get(("voice", i))
        cm = (clip or {}).get("meta") or {}
        scenes.append({"image_path": img["file_path"] if img else None,
                       "clip_path": clip["file_path"] if clip else None,
                       "clip_start": cm.get("start", 0.0), "fit": cm.get("fit", "crop"),
                       "is_source": bool(clip and clip["provider"] == "source"),
                       "keep_audio": bool(cm.get("keep_audio")) and not voice,
                       "still": bool(img and (img.get("meta") or {}).get("card")),
                       "voice_path": voice["file_path"] if voice else None, "duration": tl["duration"]})
    out = f"renders/{video_id}.mp4"
    music = assets.get(("music", 0))
    job = _worker("POST", "/render", json={
        "video_id": str(video_id), "scenes": scenes, "subtitle_path": v["subtitle_path"], "output_path": out,
        "music_path": music["file_path"] if music else None,
        "music_volume": float((music or {}).get("meta", {}).get("volume", 0.12))})
    fa = _upsert_asset(video_id, "final", 0, "video-worker", None)
    db.exec_("UPDATE media_assets SET status='processing', provider_job_id=%s, attempts=attempts+1 WHERE id=%s",
             (job["job_id"], fa["id"]))
    set_stage(video_id, "rendering")
    return {"video_id": str(video_id), "stage": "rendering", "job_id": job["job_id"]}


def check_render(video_id) -> dict:
    fa = db.one("SELECT * FROM media_assets WHERE video_id=%s AND kind='final'", (video_id,))
    if not fa or fa["status"] != "processing":
        return {"video_id": str(video_id), "stage": get_video(video_id)["stage"]}
    try:
        job = _worker("GET", f"/jobs/{fa['provider_job_id']}")
    except ProviderError as e:
        if "404" in str(e):   # worker yeniden başladıysa iş kaybolmuştur: tekrar kuyruğa al
            db.exec_("UPDATE media_assets SET status='pending' WHERE id=%s", (fa["id"],))
            set_stage(video_id, "voiced", "render işi kayboldu, yeniden denenecek")
            return {"video_id": str(video_id), "stage": "voiced", "requeued": True}
        raise
    if job["status"] == "done":
        p = config.MEDIA_ROOT / job["output_path"]
        db.exec_("UPDATE media_assets SET status='ready', file_path=%s, sha256=%s, duration_sec=%s WHERE id=%s",
                 (job["output_path"], file_sha256(p), job["duration"], fa["id"]))
        db.exec_("UPDATE videos SET stage='rendered', file_path=%s, last_error=NULL WHERE id=%s",
                 (job["output_path"], video_id))
    elif job["status"] == "failed":
        dead = _asset_failure(fa["id"], RuntimeError(job.get("error", "")), True)
        set_stage(video_id, "failed" if dead else "voiced", job.get("error", "")[:500])
        db.log_error("video-worker", "render başarısız", video_id, {"error": job.get("error", "")[:2000]})
    return {"video_id": str(video_id), "stage": get_video(video_id)["stage"], "job": job}


# =============================================================== 6) kalite kontrol
def image_dhash(p: Path, at: float = 0.0) -> str:
    """Kaynak görselin (videoda `at` saniyesindeki karenin) 256 bit fark hash'i."""
    from PIL import Image
    if p.suffix.lower() in (".mp4", ".mov", ".webm", ".mkv", ".avi", ".ogv"):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{at:.3f}", "-i", str(p), "-frames:v", "1", "-vf",
                              "scale=17:16,format=gray", "-f", "rawvideo", "-"], capture_output=True, timeout=60).stdout
        g = list(raw)
    else:
        g = list(Image.open(p).convert("L").resize((17, 16)).getdata())
    return "".join("1" if g[r * 17 + c] > g[r * 17 + c + 1] else "0" for r in range(16) for c in range(16))


def duplicate_assets(video_id) -> list[list]:
    # bölüm kartları kasıtlı olarak aynı şablondadır: tekrar kontrolüne girmez
    rows = db.all_("SELECT scene_index, kind, file_path, sha256, meta FROM media_assets WHERE video_id=%s "
                   "AND status='ready' AND kind IN ('image','video_clip') AND provider <> 'title_card' "
                   "ORDER BY kind, scene_index", (video_id,))
    by_scene: dict[int, dict] = {}
    for r in rows:   # klip varsa sahnenin görüntüsü kliptir
        if r["kind"] == "video_clip" or r["scene_index"] not in by_scene:
            by_scene[r["scene_index"]] = r
    items = sorted(by_scene.items())
    hashes = {i: image_dhash(config.MEDIA_ROOT / r["file_path"], float((r["meta"] or {}).get("start", 0)))
              for i, r in items}
    pairs = []
    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            (i, ra), (j, rb) = items[a], items[b]
            dist = sum(x != y for x, y in zip(hashes[i], hashes[j]))
            if ra["sha256"] == rb["sha256"] or dist <= 10:
                pairs.append([i, j, dist])
    return pairs


def run_qc(video_id) -> dict:
    v = get_video(video_id)
    if v["stage"] not in ("rendered", "qc_failed", "qc_passed"):
        return {"skipped": True, "stage": v["stage"]}
    timeline = _timeline(video_id)
    mids = [t["start"] + t["duration"] / 2 for t in timeline]
    p = _worker("POST", "/probe", json={"path": v["file_path"], "scene_midpoints": mids})
    lo, hi = float(db.get_setting("min_duration_sec", 20)), float(db.get_setting("max_duration_sec", 45))
    planned = sum(t["duration"] for t in timeline)
    checks, warnings = {}, []

    def check(name, ok, detail=""):
        checks[name] = {"ok": bool(ok), "detail": detail}

    check("opens", p.get("opens"), p.get("error", ""))
    if p.get("opens"):
        vs, au = p["video"] or {}, p["audio"]
        check("has_audio", au is not None and (p["mean_volume_db"] or -99) > -40,
              f"mean_volume={p.get('mean_volume_db')} dB")
        sub = v["subtitle_path"] and (config.MEDIA_ROOT / v["subtitle_path"])
        speech = db.one("SELECT count(*) c FROM media_assets WHERE video_id=%s AND (kind='voice' OR "
                        "(kind='video_clip' AND provider='source' AND (meta->>'keep_audio')::boolean))",
                        (video_id,))["c"]
        has_sub = bool(sub and sub.exists() and sub.stat().st_size > 20)
        check("has_subtitles", has_sub or not speech,
              v["subtitle_path"] if has_sub else "konuşma yok; altyazı gerekmiyor" if not speech else "yok")
        check("vertical_9_16", vs.get("height", 0) > vs.get("width", 1) and
              abs(vs.get("width", 0) / max(vs.get("height", 1), 1) - 9 / 16) < 0.01,
              f"{vs.get('width')}x{vs.get('height')}")
        vd, ad = vs.get("duration", 0), (au or {}).get("duration", 0)
        check("av_sync", abs(vd - ad) < 0.3 and abs(vd - planned) < 0.6,
              f"video={vd:.2f}s audio={ad:.2f}s planned={planned:.2f}s")
        check("duration_range", lo <= p["format_duration"] <= hi, f"{p['format_duration']:.1f}s ∈ [{lo},{hi}]")
        check("no_black_segments", not p["black_segments"], "; ".join(p["black_segments"])[:300])
        check("decodes_cleanly", not p.get("decode_errors"),
              "tüm kareler hatasız çözüldü" if not p.get("decode_errors") else "; ".join(p["decode_errors"])[:300])
        if vs.get("width") != 1080 or vs.get("height") != 1920:
            checks["vertical_9_16"] = {"ok": False, "detail": f"{vs.get('width')}x{vs.get('height')} (1080x1920 olmalı)"}
        dups = duplicate_assets(video_id)
        check("no_duplicate_scenes", not dups, f"aynı/çok benzer sahne görselleri (sahne, sahne, fark): {dups}")
        if p["duplicate_scene_pairs"]:
            warnings.append(f"render karelerinde benzer sahneler: {p['duplicate_scene_pairs']}")
        if p["freeze_segments"]:
            warnings.append(f"donuk görüntü bölümleri: {len(p['freeze_segments'])}")
        # aynı dosya başka bir videoda var mı?
        fa = db.one("SELECT sha256 FROM media_assets WHERE video_id=%s AND kind='final'", (video_id,))
        other = db.one("SELECT video_id FROM media_assets WHERE kind='final' AND sha256=%s AND video_id<>%s",
                       (fa["sha256"], video_id))
        check("unique_output", other is None, f"aynı çıktı: {other['video_id']}" if other else "")

    # lisans / kaynak kayıtları
    assets = db.all_("SELECT a.kind, a.scene_index, a.provider, a.meta, l.kind lk, l.verified, l.proof_url "
                     "FROM media_assets a LEFT JOIN licenses l ON l.id=a.license_id "
                     "WHERE a.video_id=%s AND a.kind IN ('image','video_clip','voice','music')", (video_id,))
    missing = [f"{a['kind']}#{a['scene_index']}" for a in assets if a["lk"] is None]
    unverified = [f"{a['kind']}#{a['scene_index']}" for a in assets
                  if a["lk"] in ("licensed", "cc", "public_domain") and not (a["verified"] and a["proof_url"])]
    check("licenses_complete", assets and not missing and not unverified,
          f"eksik={missing} doğrulanmamış={unverified}")
    check("script_sources", len(v["sources"] or []) > 0 or v["content_mode"] == "original_ai",
          f"{len(v['sources'] or [])} kaynak")
    if not v["sources"]:
        warnings.append("senaryoda bilgi kaynağı yok — bilgi içeren videolarda kaynak önerilir")
    check("metadata", bool(v["title"]) and len(v["title"]) <= 100 and bool(v["description"]), v["title"])

    test_assets = [a for a in assets if a["provider"] in ("local_textcard", "espeak")]
    if test_assets:
        warnings.append("TEST varlıkları kullanıldı (yerel metin kartı / espeak). Yalnızca gizli test yüklemesine "
                        "uygundur; herkese açık yayın engellenir.")
    passed = all(c["ok"] for c in checks.values())
    result = {"passed": passed, "checks": checks, "warnings": warnings,
              "public_eligible": passed and not test_assets, "probe": p}
    db.exec_("UPDATE videos SET qc_result=%s, qc_passed=%s, stage=%s WHERE id=%s",
             (Jsonb(result), passed, "qc_passed" if passed else "qc_failed", video_id))
    if not passed:
        db.log_error("qc", "kalite kontrolü başarısız", video_id,
                     {k: c for k, c in checks.items() if not c["ok"]})
    return {"video_id": str(video_id), **result}


# =============================================================== 7) onay ve yayın devri
def approve(video_id, user: str) -> dict:
    n = db.exec_("UPDATE videos SET stage='approved', approved_by=%s, approved_at=now() "
                 "WHERE id=%s AND stage='qc_passed'", (user, video_id))
    if not n:
        raise StepBlocked("yalnızca QC'den geçmiş videolar onaylanabilir")
    return {"video_id": str(video_id), "stage": "approved"}


def reject(video_id, user: str, reason: str = "") -> dict:
    db.exec_("UPDATE videos SET stage='rejected', approved_by=%s, last_error=%s WHERE id=%s "
             "AND stage IN ('qc_passed','qc_failed','rendered')", (user, reason[:500], video_id))
    return {"video_id": str(video_id), "stage": get_video(video_id)["stage"]}


def series_links(v: dict) -> tuple[str, bool]:
    """Seri bölümü için açıklama satırları (önceki bölüm, oynatma listesi) ve çocuklara özel beyanı."""
    if not v.get("series_id"):
        return "", False
    s = db.one("SELECT title, playlist_id, made_for_kids FROM series WHERE id=%s", (v["series_id"],))
    prev = db.one("SELECT youtube_url FROM videos WHERE series_id=%s AND episode_number=%s",
                  (v["series_id"], (v.get("episode_number") or 1) - 1))
    lines = [f"📺 {s['title']} — Bölüm {v.get('episode_number')}"]
    if prev and prev["youtube_url"]:
        lines.append(f"◀ Önceki bölüm: {prev['youtube_url']}")
    if s["playlist_id"]:
        lines.append(f"▶ Tüm bölümler (sırayla): https://www.youtube.com/playlist?list={s['playlist_id']}")
    lines.append("Devamı bir sonraki bölümde — kanalı takip etmeyi unutma!")
    return "\n".join(lines), bool(s["made_for_kids"])


def youtube_metadata(v: dict) -> dict:
    srcs = "\n".join(f"• {s['title']}: {s['url']}" for s in (v["sources"] or [])[:5])
    disclosure = ("Bu videoda yapay zekâ ile üretilmiş görseller ve/veya ses kullanılmıştır."
                  if v["contains_synthetic_media"] else "")
    series_text, kids = series_links(v)
    desc = "\n\n".join(x for x in [series_text, v["description"], ("Kaynaklar:\n" + srcs) if srcs else "",
                                   disclosure, "#shorts"] if x)
    return {
        "snippet": {"title": v["title"][:100], "description": desc[:4900],
                    "tags": list(v["tags"] or [])[:15], "categoryId": "1" if v.get("series_id") else "27",
                    "defaultLanguage": v["language"], "defaultAudioLanguage": v["language"]},
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": kids,
                   "containsSyntheticMedia": bool(v["contains_synthetic_media"]), "embeddable": True},
    }


def _privacy_and_schedule(conn, video_id) -> tuple[str, str | None]:
    """Varsayılan: gizli. Herkese açık / zamanlanmış yayın yalnızca ayar açıkken ve video
    test varlığı içermiyorsa (QC public_eligible) kullanılır."""
    mode = db.get_setting("upload_privacy", "private")
    if not db.get_setting("public_publishing_enabled", False) or mode == "private":
        return "private", None
    qc = conn.execute("SELECT qc_result FROM videos WHERE id=%s", (video_id,)).fetchone()["qc_result"] or {}
    if not qc.get("public_eligible"):
        return "private", None
    if mode == "public":
        return "public", None
    if mode == "unlisted":
        return "unlisted", None
    # scheduled: bir sonraki boş yayın slotu (UTC); YouTube publishAt için privacyStatus=private olmalı
    from datetime import datetime, timedelta, timezone
    slots = db.get_setting("publish_slots_utc", ["09:00", "14:00", "18:00"])
    taken = {r["publish_at"] for r in conn.execute(
        "SELECT publish_at FROM publishing_jobs WHERE publish_at > now() AND status IN ('pending','uploading','uploaded')"
    ).fetchall()}
    now = datetime.now(timezone.utc)
    for day in range(0, 14):
        for sl in sorted(slots):
            hh, mm = map(int, sl.split(":"))
            t = (now + timedelta(days=day)).replace(hour=hh, minute=mm, second=0, microsecond=0)
            if t > now + timedelta(minutes=30) and t not in taken:
                return "private", t.isoformat()
    return "private", None


def claim_next_publish() -> dict | None:
    """Onaylı ve QC'den geçmiş, henüz yüklenmemiş bir videoyu yükleme için kilitler."""
    with db.tx() as conn:
        # seri bölümleri sırayla: önceki bölüm YouTube'a yüklenmeden sonraki yüklenmez
        v = conn.execute(
            "SELECT v.id FROM videos v WHERE v.stage='approved' AND v.qc_passed AND v.youtube_video_id IS NULL "
            "AND (v.series_id IS NULL OR v.episode_number = 1 OR EXISTS (SELECT 1 FROM videos p "
            "     WHERE p.series_id=v.series_id AND p.episode_number=v.episode_number-1 "
            "     AND p.youtube_video_id IS NOT NULL)) "
            "ORDER BY v.series_id NULLS FIRST, v.episode_number, v.approved_at "
            "LIMIT 1 FOR UPDATE OF v SKIP LOCKED").fetchone()
        if not v:
            return None
        privacy, publish_at = _privacy_and_schedule(conn, v["id"])
        job = conn.execute("INSERT INTO publishing_jobs (video_id, privacy_status, publish_at, status, attempts) "
                           "VALUES (%s,%s,%s,'uploading',1) RETURNING id", (v["id"], privacy, publish_at)).fetchone()
        conn.execute("UPDATE videos SET stage='uploading' WHERE id=%s", (v["id"],))
    full = get_video(v["id"])
    meta = youtube_metadata(full)
    meta["status"]["privacyStatus"] = privacy
    if publish_at:
        meta["status"]["publishAt"] = publish_at
    size = (config.MEDIA_ROOT / full["file_path"]).stat().st_size
    series = None
    if full.get("series_id"):
        s = db.one("SELECT id, title, description, playlist_id FROM series WHERE id=%s", (full["series_id"],))
        # oynatma listesi videolarla aynı görünürlükte açılır (herkese açık yayın kapalıyken gizli)
        series = {"id": str(s["id"]), "title": s["title"], "playlist_id": s["playlist_id"],
                  "playlist_body": {"snippet": {"title": s["title"][:150],
                                                "description": (s["description"] or "")[:4900],
                                                "defaultLanguage": full["language"]},
                                    "status": {"privacyStatus": "public"
                                               if db.get_setting("public_publishing_enabled", False)
                                               else "private"}}}
    return {"job_id": str(job["id"]), "video_id": str(v["id"]), "file_path": full["file_path"],
            "file_size": size, "metadata": meta, "series": series}


def publish_result(job_id, success: bool, youtube_video_id: str | None, response: dict | None,
                   error: str | None) -> dict:
    job = db.one("SELECT * FROM publishing_jobs WHERE id=%s", (job_id,))
    if not job:
        raise KeyError("yayın işi bulunamadı")
    if success and youtube_video_id:
        privacy = (response or {}).get("status", {}).get("privacyStatus", job["privacy_status"])
        with db.tx() as conn:
            conn.execute("UPDATE publishing_jobs SET status='uploaded', youtube_video_id=%s, response=%s, error=NULL "
                         "WHERE id=%s", (youtube_video_id, Jsonb(response or {}), job_id))
            conn.execute("UPDATE videos SET stage='uploaded', publish_status=%s, youtube_video_id=%s, youtube_url=%s, "
                         "published_at=now(), last_error=NULL WHERE id=%s",
                         (privacy, youtube_video_id, f"https://youtube.com/shorts/{youtube_video_id}", job["video_id"]))
        budget.record("youtube", "videos.insert", 0, True, job["video_id"],
                      config.PRICING["youtube"]["per_upload_units"], "quota_units")
    else:
        with db.tx() as conn:
            conn.execute("UPDATE publishing_jobs SET status='failed', response=%s, error=%s WHERE id=%s",
                         (Jsonb(response or {}), (error or "")[:2000], job_id))
            # deneme hakkı varsa tekrar onaylı kuyruğa döner; yoksa failed
            conn.execute("UPDATE videos SET retries=retries+1, last_error=%s, "
                         "stage=CASE WHEN retries+1 >= %s THEN 'failed' ELSE 'approved' END WHERE id=%s",
                         ((error or "")[:500], max_retries(), job["video_id"]))
        budget.record("youtube", "videos.insert", 0, False, job["video_id"])
        db.log_error("youtube-publisher", f"yükleme başarısız: {(error or '')[:300]}", job["video_id"],
                     {"response": response or {}})
    return {"job_id": str(job_id), "video": get_video(job["video_id"])["stage"]}


# =============================================================== toplu adım çalıştırıcı (n8n bunu çağırır)
STEP_STAGES = {
    "media": ("scripted", "media_pending"),
    "voice": ("media_ready",),
    "render": ("voiced", "rendering"),
    "qc": ("rendered",),
}
STEP_FUNCS = {"media": generate_media, "voice": generate_voice, "render": start_render, "qc": run_qc}


def run_step_batch(step: str, limit: int = 3) -> dict:
    stages = STEP_STAGES[step]
    rows = db.all_("SELECT id FROM videos WHERE stage = ANY(%s) ORDER BY created_at LIMIT %s",
                   (list(stages), limit))
    results = []
    for r in rows:
        try:
            results.append(STEP_FUNCS[step](r["id"]))
        except (ProviderNotConfigured, budget.BudgetExceeded, StepBlocked) as e:
            db.log_error(f"step:{step}", str(e), r["id"])
            results.append({"video_id": str(r["id"]), "blocked": str(e)})
            break   # aynı engel diğer videolar için de geçerli: boşuna tekrar deneme
        except Exception as e:  # noqa: BLE001
            db.log_error(f"step:{step}", f"{type(e).__name__}: {e}", r["id"])
            results.append({"video_id": str(r["id"]), "error": str(e)[:300]})
    return {"step": step, "processed": len(results), "results": results}
