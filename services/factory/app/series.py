"""Seriler: her video bir serinin bölümüdür; bölümler birbirinin devamıdır ve sırayla yayınlanır.

İki seri türü:
  pd_clips  — kamu malı / lisanslı bir film sahne geçişlerinden bölümlere ayrılır; film baştan sona
              sırayla anlatılır. Bölüm = giriş kartı (Türkçe anlatım) + film kesiti (orijinal ses) + "devamı" kartı.
  original  — özgün yapay zekâ bölümleri; senaryo önceki bölümlerin özetiyle yazılır ve merak uyandıran
              bir kapanışla biter.

Kaynak filmler yalnızca lisans kanıtıyla kaydedilir; kanıtı olmayan kesit QC'den geçmez.
"""
from __future__ import annotations

from pathlib import Path

from psycopg.types.json import Jsonb

from . import budget, config, db, pipeline
from .providers import ProviderError, ProviderNotConfigured

SOURCES_DIR = "sources"
# bölüm = giriş kartı (~3 sn) + kesit + kapanış kartı (~3 sn) → toplam 20-45 sn aralığında kalmalı
CLIP_MIN, CLIP_TARGET, CLIP_MAX = 18.0, 26.0, 34.0
LICENSE_KINDS = {"public_domain": "Kamu malı (Public Domain)", "cc": "Creative Commons", "licensed": "Lisanslı / izinli"}


# ------------------------------------------------------------------ bölüm planı
def plan_segments(duration: float, cuts: list[float], lo: float = CLIP_MIN, target: float = CLIP_TARGET,
                  hi: float = CLIP_MAX) -> list[dict]:
    """Filmi sahne geçişlerinden, hedef uzunluğa en yakın parçalara böler.
    Sahne geçişi bulunamayan aralıkta hedef uzunlukta kesilir."""
    segs: list[list[float]] = []
    start = 0.0
    while duration - start > 0.5:
        remaining = duration - start
        if remaining <= hi:
            if remaining >= lo or not segs:
                segs.append([start, duration])
            elif remaining >= 6 and segs[-1][1] - segs[-1][0] + remaining <= hi + 2:
                segs[-1][1] = duration          # kısa kalan son parça önceki bölüme eklenir
            elif remaining >= 6:
                segs.append([start, duration])  # (kısa son bölüm; QC süre kontrolünde görünür)
            # <6 sn kalan kısım (genelde jenerik) atlanır
            break
        cands = [c for c in cuts if start + lo <= c <= start + hi]
        end = min(cands, key=lambda c: abs(c - (start + target))) if cands else start + target
        segs.append([start, end])
        start = end
    return [{"start": round(s, 2), "end": round(e, 2)} for s, e in segs]


def _fmt(sec: float) -> str:
    return f"{int(sec // 60)}:{int(sec % 60):02d}"


# ------------------------------------------------------------------ kaynak filmler
def _media_file(folder: str, name: str) -> str:
    """data/<folder>/ altındaki bir dosyanın MEDIA_ROOT'a göre yolunu döndürür (klasör dışına çıkılamaz)."""
    base = (config.MEDIA_ROOT / folder).resolve()
    path = (base / name).resolve()
    if base not in path.parents or not path.is_file():
        raise ValueError(f"dosya bulunamadı: data/{folder}/{name}")
    return f"{folder}/{path.relative_to(base)}"


def register_source(file_name: str, title: str, license_kind: str, source_url: str | None,
                    proof_url: str | None, confirmed: bool, user: str, license_name: str | None = None,
                    notes: str | None = None, subtitle_file: str | None = None) -> dict:
    if license_kind not in LICENSE_KINDS:
        raise ValueError(f"lisans türü: {sorted(LICENSE_KINDS)}")
    rel = _media_file(SOURCES_DIR, file_name)
    sub_rel = None
    if subtitle_file:
        sub_rel = _media_file(SOURCES_DIR, subtitle_file)
        cues = pipeline.parse_srt((config.MEDIA_ROOT / sub_rel).read_text(encoding="utf-8-sig"))
        if not cues:
            raise ValueError("altyazı dosyası okunamadı (SRT biçiminde olmalı)")
    if db.one("SELECT 1 FROM sources WHERE file_path=%s", (rel,)):
        raise ValueError("bu dosya zaten kayıtlı")
    verified = bool(confirmed and proof_url)
    lic = db.one("INSERT INTO licenses (kind, provider, license_name, source_url, proof_url, verified, verified_by, notes) "
                 "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                 (license_kind, title, license_name or LICENSE_KINDS[license_kind], source_url, proof_url,
                  verified, user if verified else None, notes))
    info = pipeline._worker("POST", "/scenes", json={"path": rel})
    segs = plan_segments(info["duration"], info["cuts"])
    row = db.one("INSERT INTO sources (title, file_path, duration_sec, license_id, source_url, segments, notes, "
                 "subtitle_path) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                 (title, rel, info["duration"], lic["id"], source_url, Jsonb(segs), notes, sub_rel))
    return {"source_id": str(row["id"]), "duration_sec": info["duration"], "scene_cuts": len(info["cuts"]),
            "episodes": len(segs), "license_verified": verified, "has_audio": info["has_audio"],
            "subtitles": bool(sub_rel),
            "warning": None if verified else "Lisans doğrulanmadı: bu kaynaktan üretilen bölümler QC'den geçmez."}


def list_sources() -> list[dict]:
    return db.all_("SELECT s.id, s.title, s.file_path, s.duration_sec, jsonb_array_length(s.segments) episodes, "
                   "s.source_url, s.subtitle_path, l.kind license_kind, l.license_name, l.verified, l.proof_url, "
                   "s.created_at "
                   "FROM sources s JOIN licenses l ON l.id=s.license_id ORDER BY s.created_at DESC")


# ------------------------------------------------------------------ seriler
def create_series(title: str, kind: str, description: str = "", source_id: str | None = None,
                  brief: str | None = None, made_for_kids: bool = False, buffer_size: int = 2,
                  language: str = "tr", narration: bool = True, keep_source_audio: bool = True,
                  music_file: str | None = None, music_license_url: str | None = None,
                  music_confirmed: bool = False, user: str = "panel") -> dict:
    if kind not in ("pd_clips", "original"):
        raise ValueError("kind: pd_clips | original")
    if kind == "pd_clips" and not source_id:
        raise ValueError("pd_clips serisi için kaynak film seçilmeli")
    if kind == "original" and not (brief or "").strip():
        raise ValueError("özgün seri için konu/format tarifi (brief) gerekli")
    music_rel, music_lic = None, None
    if music_file:
        music_rel = _media_file("music", music_file)
        verified = bool(music_confirmed and music_license_url)
        music_lic = db.one("INSERT INTO licenses (kind, provider, license_name, source_url, proof_url, verified, "
                           "verified_by) VALUES ('licensed', %s, %s, %s, %s, %s, %s) RETURNING id",
                           (music_file, "Arka plan müziği (YouTube Ses Kitaplığı / lisanslı)", music_license_url,
                            music_license_url, verified, user if verified else None))["id"]
    if kind == "pd_clips" and not narration and not keep_source_audio and not music_rel:
        raise ValueError("seslendirme ve film sesi kapalıysa arka plan müziği seçilmeli (sessiz Shorts izlenmez)")
    row = db.one("INSERT INTO series (title, kind, description, source_id, brief, made_for_kids, buffer_size, "
                 "language, narration, keep_source_audio, music_path, music_license_id) "
                 "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                 (title.strip(), kind, description, source_id, brief, made_for_kids, max(1, min(buffer_size, 5)),
                  language, narration, keep_source_audio, music_rel, music_lic))
    return {"series_id": str(row["id"])}


def list_series() -> list[dict]:
    return db.all_("""
        SELECT s.*, src.title AS source_title, jsonb_array_length(src.segments) AS total_episodes,
               (SELECT count(*) FROM videos v WHERE v.series_id=s.id) AS produced,
               (SELECT count(*) FROM videos v WHERE v.series_id=s.id AND v.youtube_video_id IS NOT NULL) AS uploaded
        FROM series s LEFT JOIN sources src ON src.id=s.source_id ORDER BY s.created_at DESC""")


def set_status(series_id: str, status: str) -> None:
    if status not in ("active", "paused", "finished"):
        raise ValueError("status: active | paused | finished")
    db.exec_("UPDATE series SET status=%s WHERE id=%s", (status, series_id))


def set_playlist(series_id: str, playlist_id: str) -> dict:
    db.exec_("UPDATE series SET playlist_id=%s WHERE id=%s AND playlist_id IS NULL", (playlist_id, series_id))
    return {"playlist_id": db.one("SELECT playlist_id FROM series WHERE id=%s", (series_id,))["playlist_id"]}


def _series(series_id) -> dict:
    s = db.one("SELECT * FROM series WHERE id=%s", (series_id,))
    if not s:
        raise KeyError(f"seri bulunamadı: {series_id}")
    return s


def _card(s, n, kind, line, text) -> dict:
    c = {"kind": "card", "card_series": s["title"], "card_episode": n, "card_type": kind,
         "card_line": line, "text": text if s.get("narration", True) else "", "visual_prompt": ""}
    if not c["text"]:
        c["duration"] = 3.0          # seslendirmesiz kart: yazı ekranda okunacak kadar
    return c


def _music(s: dict) -> dict | None:
    if not s.get("music_path"):
        return None
    # film sesi ve seslendirme yoksa müzik öne çıkar; varsa altta kalır
    alone = not s.get("narration", True) and not s.get("keep_source_audio", True)
    return {"path": s["music_path"], "license_id": str(s["music_license_id"]), "volume": 0.9 if alone else 0.15}


def _pd_episode(s: dict, n: int) -> dict:
    src = db.one("SELECT s.*, l.license_name FROM sources s JOIN licenses l ON l.id=s.license_id WHERE s.id=%s",
                 (s["source_id"],))
    segs = src["segments"]
    if n > len(segs):
        db.exec_("UPDATE series SET status='finished' WHERE id=%s", (s["id"],))
        raise pipeline.StepBlocked(f"'{s['title']}' serisinin tüm bölümleri üretildi ({len(segs)})")
    seg, last = segs[n - 1], n == len(segs)
    intro = f"{s['title']}. Bölüm {n}. " + ("Başlıyoruz!" if n == 1 else "Kaldığımız yerden devam ediyoruz!")
    outro = ("Ve serinin sonu! Yeni seride görüşmek üzere." if last
             else "Devamı bir sonraki bölümde! Kaçırmamak için takip et.")
    scenes = [
        _card(s, n, "intro", "İlk bölüm" if n == 1 else "Kaldığımız yerden devam!", intro),
        {"kind": "source_clip", "source": {"id": str(src["id"]), "start": seg["start"], "end": seg["end"],
                                           "subtitles": src.get("subtitle_path")},
         "keep_audio": bool(s.get("keep_source_audio", True)), "text": "", "visual_prompt": ""},
        _card(s, n, "finale" if last else "outro", "Serinin sonu" if last else "Devamı bir sonraki bölümde", outro),
    ]
    words = [w for w in s["title"].replace("|", " ").split() if len(w) > 2][:5]
    return {
        "hook": intro, "scenes_full": scenes,
        "title": f"{s['title']} | Bölüm {n}"[:100],
        "description": (s["description"] or "").strip() + f"\n\nBu bölüm: “{src['title']}” "
                       f"{_fmt(seg['start'])}–{_fmt(seg['end'])} arası.",
        "tags": [*words, "çizgi film", "klasik çizgi film", f"bölüm {n}", "shorts"],
        "sources": [{"title": f"{src['title']} — {src['license_name']}", "url": src["source_url"] or ""}],
        "_summary": f"{src['title']} {_fmt(seg['start'])}-{_fmt(seg['end'])}",
        "_content_mode": "licensed_transform", "_synthetic": False, "music": _music(s),
    }


EPISODE_SCHEMA = {
    **pipeline.SCRIPT_SCHEMA,
    "required": [*pipeline.SCRIPT_SCHEMA["required"], "episode_summary", "next_teaser"],
    "properties": {**pipeline.SCRIPT_SCHEMA["properties"],
                   "episode_summary": {"type": "string"}, "next_teaser": {"type": "string"}},
}


def _wrap_original(s: dict, n: int, d: dict) -> dict:
    """LLM'in (veya elle yazılmış) bölüm senaryosunu seri formatına sokar."""
    out = dict(d)
    out["title"] = f"{s['title']} | Bölüm {n}: {d['title']}"[:100]
    teaser = d.get("next_teaser") or "Devamı bir sonraki bölümde!"
    out["scenes_full"] = ([{"text": d["hook"], "visual_prompt": d["hook_visual_prompt"]}] + d["scenes"] +
                          [_card(s, n, "outro", "Devamı bir sonraki bölümde", teaser)])
    out["_summary"] = d.get("episode_summary") or d["description"]
    out["_content_mode"], out["_synthetic"] = "original_ai", True
    errs = pipeline.validate_script(d)
    if errs:
        raise ValueError("; ".join(errs))
    return out


def _original_episode(s: dict, n: int) -> dict:
    llm = pipeline.provider_for("llm")
    if llm is None:
        raise ProviderNotConfigured("LLM sağlayıcısı seçilmemiş")
    prev = db.all_("SELECT episode_number, episode_summary FROM videos WHERE series_id=%s AND episode_number < %s "
                   "ORDER BY episode_number DESC LIMIT 6", (s["id"], n))
    recap = "\n".join(f"Bölüm {p['episode_number']}: {p['episode_summary']}" for p in reversed(prev)) or "(ilk bölüm)"
    lo, hi = db.get_setting("min_duration_sec", 20), db.get_setting("max_duration_sec", 45)
    budget.ensure_budget(llm.estimated_cost)
    prompt = (
        f"Seri: {s['title']}\nSeri tarifi: {s['brief']}\nDil: {s['language']}\nŞimdi yazılacak: Bölüm {n}\n"
        f"Önceki bölümler:\n{recap}\n\n"
        f"{lo}-{hi} saniyelik bir YouTube Shorts bölümü yaz (toplam {int(lo * pipeline.WORDS_PER_SEC)}-"
        f"{int(hi * pipeline.WORDS_PER_SEC)} kelime).\n"
        "- hook: önceki bölüme kısa bir gönderme yaparak başlasın (ilk bölümse seriyi tanıtsın), en fazla 14 kelime.\n"
        "- Bölüm kendi içinde anlamlı olsun ama hikâye/konu önceki bölümün devamı olsun; tekrar etme.\n"
        "- next_teaser: bir sonraki bölüm için merak uyandıran tek cümle (yalan vaat yok).\n"
        "- episode_summary: bu bölümün 1 cümlelik özeti (sonraki bölümlerde hatırlatmak için).\n"
        "- scenes: 3-5 sahne; visual_prompt İngilizce, serinin görsel stiliyle tutarlı; gerçek kişi, marka, logo, "
        "telifli karakter yok.\n- title: bölümün kendi alt başlığı (seri adı ve bölüm numarası otomatik eklenir).\n"
        "- Bilgiler doğru olsun; sources alanına güvenilir kaynak ekle. Başka üreticilerin içeriğini kopyalama.")
    try:
        res = llm.generate_json("Sen ardışık izlenen Shorts serileri yazan bir senaristsin. Sadece istenen JSON'u üret.",
                                prompt, EPISODE_SCHEMA)
    except ProviderError:
        budget.record(llm.name, "series_script", 0, False)
        raise
    budget.record(llm.name, "series_script", res.cost_usd, True, units=res.input_tokens + res.output_tokens,
                  unit_type="tokens")
    return _wrap_original(s, n, res.data)


def _save_episode(s: dict, n: int, d: dict, provider: str) -> dict:
    out = pipeline.save_script(d, None, provider, s["language"], d["_content_mode"], series_id=s["id"],
                               episode_number=n, episode_summary=d["_summary"], synthetic=d["_synthetic"])
    db.exec_("UPDATE series SET next_episode=%s WHERE id=%s AND next_episode=%s", (n + 1, s["id"], n))
    return {**out, "series": s["title"], "episode": n}


def next_episode(series_id: str) -> dict:
    s = _series(series_id)
    if s["status"] != "active":
        raise pipeline.StepBlocked(f"seri aktif değil: {s['status']}")
    n = s["next_episode"]
    d = _pd_episode(s, n) if s["kind"] == "pd_clips" else _original_episode(s, n)
    return _save_episode(s, n, d, "series:pd" if s["kind"] == "pd_clips" else "series:llm")


def add_manual_episode(series_id: str, script: dict, user: str) -> dict:
    """Özgün seriye elle yazılmış bölüm ekler (LLM anahtarı olmadan da seri ilerletilebilir)."""
    s = _series(series_id)
    if s["kind"] != "original":
        raise ValueError("elle bölüm yalnızca özgün serilere eklenir")
    n = s["next_episode"]
    return _save_episode(s, n, _wrap_original(s, n, script), f"manual:{user}")


def run_batch() -> dict:
    """Aktif her seride, yayınlanmayı bekleyen bölüm sayısı tampon boyutunun altındaysa sonraki bölümü üretir."""
    results = []
    for s in db.all_("SELECT * FROM series WHERE status='active' ORDER BY created_at"):
        pending = db.one("SELECT count(*) c FROM videos WHERE series_id=%s AND youtube_video_id IS NULL "
                         "AND stage NOT IN ('failed','rejected')", (s["id"],))["c"]
        if pending >= s["buffer_size"]:
            results.append({"series": s["title"], "skipped": f"{pending} bölüm yayın bekliyor"})
            continue
        try:
            results.append(next_episode(s["id"]))
        except (ProviderNotConfigured, budget.BudgetExceeded, pipeline.StepBlocked, ValueError) as e:
            db.log_error("series", f"{s['title']}: {e}")
            results.append({"series": s["title"], "blocked": str(e)})
    return {"processed": len(results), "results": results}
