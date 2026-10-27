"""Analitik kayıt, performans optimizasyonu ve hata/limit izleme.

Metrik kaynakları (n8n Workflow 8 bu ayrımla çağırır):
  YouTube Data API v3  videos.list?part=statistics   -> viewCount, likeCount, commentCount (anlık toplam)
  YouTube Analytics v2 reports.query                -> views, estimatedMinutesWatched, averageViewDuration,
                                                       averageViewPercentage, subscribersGained/Lost (günlük)
                       dimensions=insightTrafficSourceType -> trafik kaynakları
                       dimensions=elapsedVideoTimeRatio, metrics=audienceWatchRatio -> izleyici tutma eğrisi
Analytics API ayrı bir OAuth kapsamı ister: yt-analytics.readonly.
"""
from __future__ import annotations

import json
import statistics
from datetime import date

from psycopg.types.json import Jsonb

from . import budget, db, pipeline
from .providers import ProviderNotConfigured

MIN_VIDEOS_FOR_CONCLUSIONS = 10
MIN_AGE_HOURS = 48


def targets() -> list[dict]:
    rows = db.all_("SELECT id, youtube_video_id, published_at FROM videos WHERE youtube_video_id IS NOT NULL "
                   "ORDER BY published_at DESC LIMIT 200")
    return [{"video_id": str(r["id"]), "youtube_video_id": r["youtube_video_id"],
             "published_at": r["published_at"] and r["published_at"].date().isoformat()} for r in rows]


def _vid(yt_id: str):
    r = db.one("SELECT id FROM videos WHERE youtube_video_id=%s", (yt_id,))
    return r and r["id"]


def ingest_data_api(payload: dict) -> int:
    """videos.list yanıtını (items[]) kaydeder."""
    n, today = 0, date.today()
    for it in payload.get("items", []):
        st = it.get("statistics", {})
        db.exec_("INSERT INTO analytics (video_id, youtube_video_id, day, source_api, views, likes, comments, raw) "
                 "VALUES (%s,%s,%s,'data_api',%s,%s,%s,%s) ON CONFLICT (youtube_video_id, day, source_api) DO UPDATE "
                 "SET views=EXCLUDED.views, likes=EXCLUDED.likes, comments=EXCLUDED.comments, raw=EXCLUDED.raw, "
                 "collected_at=now()",
                 (_vid(it["id"]), it["id"], today, int(st.get("viewCount", 0)), int(st.get("likeCount", 0)),
                  int(st.get("commentCount", 0)), Jsonb(it)))
        n += 1
    return n


def _rows(report: dict) -> list[dict]:
    cols = [h["name"] for h in report.get("columnHeaders", [])]
    return [dict(zip(cols, r)) for r in report.get("rows", []) or []]


def ingest_analytics_api(payload: dict) -> int:
    """reports.query yanıtları. payload: {"daily": report, "traffic": {ytid: report}, "retention": {ytid: report}}"""
    n = 0
    for r in _rows(payload.get("daily", {})):
        yt = r["video"]
        db.exec_(
            "INSERT INTO analytics (video_id, youtube_video_id, day, source_api, views, estimated_minutes_watched, "
            "average_view_duration_sec, average_view_percentage, subscribers_gained, subscribers_lost, raw) "
            "VALUES (%s,%s,%s,'analytics_api',%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (youtube_video_id, day, source_api) "
            "DO UPDATE SET views=EXCLUDED.views, estimated_minutes_watched=EXCLUDED.estimated_minutes_watched, "
            "average_view_duration_sec=EXCLUDED.average_view_duration_sec, "
            "average_view_percentage=EXCLUDED.average_view_percentage, subscribers_gained=EXCLUDED.subscribers_gained, "
            "subscribers_lost=EXCLUDED.subscribers_lost, raw=EXCLUDED.raw, collected_at=now()",
            (_vid(yt), yt, r.get("day", date.today().isoformat()), r.get("views"), r.get("estimatedMinutesWatched"),
             r.get("averageViewDuration"), r.get("averageViewPercentage"), r.get("subscribersGained"),
             r.get("subscribersLost"), Jsonb(r)))
        n += 1
    for key, col in (("traffic", "traffic_sources"), ("retention", "retention")):
        for yt, rep in (payload.get(key) or {}).items():
            db.exec_(f"INSERT INTO analytics (video_id, youtube_video_id, day, source_api, {col}) "
                     f"VALUES (%s,%s,%s,'analytics_api',%s) ON CONFLICT (youtube_video_id, day, source_api) "
                     f"DO UPDATE SET {col}=EXCLUDED.{col}, collected_at=now()",
                     (_vid(yt), yt, date.today(), Jsonb(_rows(rep))))
    return n


def performance_table() -> list[dict]:
    return db.all_("""
        SELECT v.id, v.topic, s.title, s.hook, ci.category, v.youtube_url, v.published_at,
               (SELECT max(views) FROM analytics a WHERE a.video_id=v.id AND a.source_api='data_api') AS views,
               (SELECT avg(average_view_percentage) FROM analytics a WHERE a.video_id=v.id
                   AND a.source_api='analytics_api') AS avg_view_pct,
               (SELECT sum(subscribers_gained) FROM analytics a WHERE a.video_id=v.id
                   AND a.source_api='analytics_api') AS subs
        FROM videos v JOIN scripts s ON s.id=v.script_id LEFT JOIN content_ideas ci ON ci.id=s.idea_id
        WHERE v.youtube_video_id IS NOT NULL ORDER BY v.published_at DESC""")


OPT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["ideas", "observations"],
              "properties": {"observations": {"type": "array", "items": {"type": "string"}},
                             "ideas": pipeline.IDEAS_SCHEMA["properties"]["ideas"]}}


def optimize() -> dict:
    """Kategori/başlık/giriş performansını karşılaştırır, yeterli veri varsa yeni fikir önerir.
    Asla video silmez, yeniden yayınlamaz veya gizlilik değiştirmez."""
    rows = [r for r in performance_table() if r["views"] is not None and r["published_at"]
            and (date.today() - r["published_at"].date()).days * 24 >= MIN_AGE_HOURS]
    if len(rows) < MIN_VIDEOS_FOR_CONCLUSIONS:
        return {"status": "insufficient_data", "videos_with_data": len(rows),
                "required": MIN_VIDEOS_FOR_CONCLUSIONS,
                "message": f"En az {MIN_VIDEOS_FOR_CONCLUSIONS} videonun {MIN_AGE_HOURS} saatlik verisi gerekli; "
                           "kesin sonuç çıkarılmadı."}
    by_cat: dict[str, list[int]] = {}
    for r in rows:
        by_cat.setdefault(r["category"] or "diğer", []).append(r["views"])
    cats = sorted(({"category": c, "videos": len(v), "median_views": statistics.median(v)}
                   for c, v in by_cat.items()), key=lambda x: -x["median_views"])
    ranked = sorted(rows, key=lambda r: -(r["views"] or 0))
    top, bottom = ranked[: max(3, len(ranked) // 5)], ranked[-max(3, len(ranked) // 5):]
    result = {"status": "ok", "categories": cats,
              "top": [{"title": r["title"], "hook": r["hook"], "views": r["views"]} for r in top],
              "bottom": [{"title": r["title"], "hook": r["hook"], "views": r["views"]} for r in bottom]}
    llm = pipeline.provider_for("llm")
    if llm is None:
        result["ideas"] = "LLM sağlayıcısı yok; yalnızca istatistik raporlandı"
        return result
    try:
        budget.ensure_budget(llm.estimated_cost)
        res = llm.generate_json(
            "Sen veri odaklı bir YouTube Shorts stratejistisin. Küçük örneklemde aşırı genelleme yapma.",
            "Performans verisi:\n" + json.dumps(result, ensure_ascii=False, default=str) +
            "\n\nBaşarılı ve başarısız videoların başlık/giriş farklarını gözlemle (observations), sonra "
            "tekrar etmeyen 5 yeni konu öner (ideas). Score 0-100.", OPT_SCHEMA)
    except (ProviderNotConfigured, budget.BudgetExceeded) as e:
        result["ideas"] = str(e)
        return result
    budget.record(llm.name, "optimizer", res.cost_usd, True, units=res.input_tokens + res.output_tokens,
                  unit_type="tokens")
    result["observations"] = res.data["observations"]
    result["ideas"] = [{"topic": i["topic"], **pipeline.add_idea(i["topic"], i["category"], i["angle"],
                                                                  "optimizer", i["score"])}
                       for i in res.data["ideas"]]
    return result


def monitor() -> dict:
    """Workflow 10: takılan işler, hata özeti, maliyet ve kota durumu."""
    stuck = db.all_("SELECT id, stage, updated_at FROM videos WHERE stage IN ('rendering','uploading','media_pending') "
                    "AND updated_at < now() - interval '45 minutes'")
    for s in stuck:
        if s["stage"] == "uploading":   # yarım kalan yükleme: tekrar denemeye aç, aynı videoyu iki kez yükleme
            db.exec_("UPDATE publishing_jobs SET status='failed', error='zaman aşımı (monitor)' "
                     "WHERE video_id=%s AND status='uploading'", (s["id"],))
            db.exec_("UPDATE videos SET stage='approved', retries=retries+1 WHERE id=%s AND youtube_video_id IS NULL",
                     (s["id"],))
    db.exec_("UPDATE videos SET stage='failed', last_error='deneme sınırı aşıldı' "
             "WHERE retries >= %s AND stage NOT IN ('failed','uploaded','published','rejected')",
             (pipeline.max_retries(),))
    limit = float(db.get_setting("daily_cost_limit_usd", 5.0))
    spent = budget.spent_today()
    uploads_today = db.one("SELECT count(*) c FROM api_usage WHERE provider='youtube' AND operation='videos.insert' "
                           "AND success AND created_at >= date_trunc('day', now())")["c"]
    errors = db.all_("SELECT source, count(*) c, max(created_at) last FROM error_logs "
                     "WHERE created_at > now() - interval '24 hours' AND NOT resolved GROUP BY source ORDER BY c DESC")
    failed = db.all_("SELECT id, topic, last_error FROM videos WHERE stage='failed' "
                     "AND updated_at > now() - interval '24 hours'")
    alerts = []
    if spent >= limit * 0.8:
        alerts.append(f"Günlük maliyetin %{spent / limit * 100:.0f}'i kullanıldı ({spent:.2f}$ / {limit:.2f}$)")
    if uploads_today >= 90:
        alerts.append(f"YouTube yükleme kotasına yaklaşılıyor: bugün {uploads_today} yükleme")
    if failed:
        alerts.append(f"Son 24 saatte {len(failed)} video başarısız oldu")
    return {"cost_today_usd": round(spent, 4), "cost_limit_usd": limit, "uploads_today": uploads_today,
            "stuck_recovered": len(stuck), "errors_24h": errors, "failed_videos_24h": failed, "alerts": alerts}
