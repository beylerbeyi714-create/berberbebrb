"""AI Shorts Factory — factory-api: n8n'in çağırdığı REST API + yönetim paneli."""
from __future__ import annotations

import hmac
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

import bcrypt
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from . import analytics, budget, config, db, pipeline, providers, series
from .providers import ProviderError, ProviderNotConfigured


@asynccontextmanager
async def lifespan(_app):
    db.pool.open(wait=True, timeout=60)
    yield
    db.pool.close()


app = FastAPI(title="AI Shorts Factory", version="1.0.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
if not config.SESSION_SECRET or len(config.SESSION_SECRET) < 32:
    raise RuntimeError("SESSION_SECRET en az 32 karakter olmalı")
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET, same_site="strict",
                   https_only=config.COOKIE_SECURE, max_age=12 * 3600)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# ------------------------------------------------------------------ kimlik doğrulama
def require_auth(request: Request) -> str:
    """n8n: X-API-Key başlığı. Panel: oturum çerezi + X-Panel başlığı (CSRF koruması)."""
    key = request.headers.get("x-api-key", "")
    if config.API_TOKEN and key and hmac.compare_digest(key, config.API_TOKEN):
        return "n8n"
    user = request.session.get("user")
    if user and (request.method == "GET" or request.headers.get("x-panel") == "1"):
        return user
    raise HTTPException(401, "yetkisiz")


_login_attempts: dict[str, list[float]] = defaultdict(list)


@app.exception_handler(ProviderNotConfigured)
async def _h1(_r, e):
    db.log_error("api", f"sağlayıcı yapılandırılmamış: {e}")
    return JSONResponse({"code": "provider_not_configured", "message": str(e), "detail": str(e)}, status_code=424)


@app.exception_handler(budget.BudgetExceeded)
async def _h2(_r, e):
    db.log_error("budget", str(e))
    return JSONResponse({"code": "budget_exceeded", "message": str(e), "detail": str(e)}, status_code=402)


@app.exception_handler(pipeline.StepBlocked)
async def _h3(_r, e):
    return JSONResponse({"code": "blocked", "message": str(e), "detail": str(e)}, status_code=409)


@app.exception_handler(ProviderError)
async def _h4(_r, e):
    db.log_error("provider", str(e))
    return JSONResponse({"code": "provider_error", "message": str(e), "retryable": e.retryable, "detail": str(e)}, status_code=502)


@app.exception_handler(ValueError)
async def _h5(_r, e):
    return JSONResponse({"code": "invalid", "message": str(e), "detail": str(e)}, status_code=422)


@app.exception_handler(KeyError)
async def _h6(_r, e):
    return JSONResponse({"code": "not_found", "message": str(e), "detail": str(e)}, status_code=404)


# ------------------------------------------------------------------ genel
@app.get("/health")
def health():
    db.one("SELECT 1")
    return {"status": "ok"}


def _worker_health() -> dict:
    try:
        return httpx.get(config.WORKER_URL + "/health", timeout=5).json()
    except Exception as e:  # noqa: BLE001
        return {"status": "unreachable", "error": str(e)[:200]}


@app.get("/api/status")
def status(_=Depends(require_auth)):
    stages = {r["stage"]: r["c"] for r in db.all_("SELECT stage, count(*) c FROM videos GROUP BY stage")}
    return {"secrets_present": config.secret_status(), "providers_available": providers.available(),
            "settings": db.all_settings(), "worker": _worker_health(), "video_stages": stages,
            "cost_today_usd": budget.spent_today()}


class SettingsPatch(BaseModel):
    values: dict


ALLOWED_SETTINGS = {"automation_enabled", "daily_video_target", "language", "categories", "min_duration_sec",
                    "max_duration_sec", "upload_privacy", "require_manual_approval", "public_publishing_enabled",
                    "daily_cost_limit_usd", "max_retries", "content_mode", "providers", "publish_slots_utc"}


@app.get("/api/settings")
def get_settings(_=Depends(require_auth)):
    return db.all_settings()


@app.put("/api/settings")
def put_settings(body: SettingsPatch, who=Depends(require_auth)):
    if who == "n8n":
        raise HTTPException(403, "ayarlar yalnızca panelden değiştirilebilir")
    bad = set(body.values) - ALLOWED_SETTINGS
    if bad:
        raise HTTPException(422, f"bilinmeyen ayar: {sorted(bad)}")
    v = body.values
    if "upload_privacy" in v and v["upload_privacy"] not in ("private", "unlisted", "public", "scheduled"):
        raise HTTPException(422, "upload_privacy: private|unlisted|public|scheduled")
    if "providers" in v:
        for kind, name in v["providers"].items():
            if name not in (None, "", "none") and name not in providers.available().get(kind, []):
                raise HTTPException(422, f"{kind} için bilinmeyen sağlayıcı {name}")
    for k, val in v.items():
        db.set_setting(k, val)
    db.log_run("panel:settings", "success", detail={"by": who, "keys": sorted(v)})
    return db.all_settings()


@app.get("/api/should-run")
def should_run(workflow: str, _=Depends(require_auth)):
    """n8n zamanlanmış tetikleyicileri buna sorar: otomasyon açık mı, günlük hedef doldu mu?"""
    enabled = bool(db.get_setting("automation_enabled", False))
    target = int(db.get_setting("daily_video_target", 3))
    made = db.one("SELECT count(*) c FROM videos WHERE created_at >= date_trunc('day', now())")["c"]
    run = enabled and (workflow not in ("research", "script", "series") or made < target)
    reason = "otomasyon kapalı" if not enabled else (
        f"günlük hedef doldu ({made}/{target})" if not run else "ok")
    return {"run": run, "reason": reason, "made_today": made, "target": target}


# ------------------------------------------------------------------ fikirler / senaryolar
class IdeaIn(BaseModel):
    topic: str
    category: str | None = None
    angle: str | None = None
    score: float = 50


@app.get("/api/ideas")
def list_ideas(status: str = "queued", _=Depends(require_auth)):
    return db.all_("SELECT * FROM content_ideas WHERE status=%s ORDER BY score DESC, created_at LIMIT 200", (status,))


@app.post("/api/ideas")
def create_idea(body: IdeaIn, who=Depends(require_auth)):
    return pipeline.add_idea(body.topic, body.category, body.angle, f"manual:{who}", body.score,
                             db.get_setting("language", "tr"))


@app.post("/api/research/run")
def research(count: int = 10, _=Depends(require_auth)):
    return pipeline.research(count)


class ScriptGen(BaseModel):
    idea_id: str | None = None


@app.post("/api/scripts/generate")
def gen_script(body: ScriptGen, _=Depends(require_auth)):
    return pipeline.generate_script(body.idea_id)


class ManualScript(BaseModel):
    idea_id: str | None = None
    topic: str | None = None
    script: dict
    content_mode: str = "original_ai"


@app.post("/api/scripts")
def manual_script(body: ManualScript, who=Depends(require_auth)):
    """Elle yazılmış (veya dışarıda üretilmiş) senaryoyu kuyruğa alır."""
    idea_id = body.idea_id
    if not idea_id and body.topic:
        r = pipeline.add_idea(body.topic, None, None, f"manual:{who}", 50, db.get_setting("language", "tr"))
        if not r["created"]:
            raise ValueError(r["reason"])
        idea_id = r["id"]
    missing = set(pipeline.SCRIPT_SCHEMA["required"]) - set(body.script)
    if missing:
        raise ValueError(f"eksik alanlar: {sorted(missing)}")
    return pipeline.save_script(body.script, idea_id, f"manual:{who}", db.get_setting("language", "tr"),
                                body.content_mode)


# ------------------------------------------------------------------ seriler ve kaynak filmler
class SourceIn(BaseModel):
    file_name: str                      # data/sources/ altındaki dosya adı
    title: str
    license_kind: str                   # public_domain | cc | licensed
    license_name: str | None = None
    source_url: str | None = None
    proof_url: str | None = None        # lisans kanıtı (kamu malı işareti, CC sayfası, izin belgesi)
    confirmed: bool = False             # "lisansı kendim kontrol ettim" onayı
    notes: str | None = None
    subtitle_file: str | None = None    # data/sources/ altındaki Türkçe .srt (filmin tamamı için)


@app.get("/api/sources")
def get_sources(_=Depends(require_auth)):
    return series.list_sources()


@app.post("/api/sources")
def post_source(body: SourceIn, who=Depends(require_auth)):
    if who == "n8n":
        raise HTTPException(403, "kaynak lisansı yalnızca panelden onaylanabilir")
    return series.register_source(body.file_name, body.title, body.license_kind, body.source_url, body.proof_url,
                                  body.confirmed, who, body.license_name, body.notes, body.subtitle_file)


class SeriesIn(BaseModel):
    title: str
    kind: str                           # pd_clips | original
    description: str = ""
    source_id: str | None = None
    brief: str | None = None
    made_for_kids: bool = False
    buffer_size: int = 2
    narration: bool = True              # kartlarda Türkçe seslendirme
    keep_source_audio: bool = True      # filmin kendi sesi
    music_file: str | None = None       # data/music/ altındaki lisanslı parça
    music_license_url: str | None = None
    music_confirmed: bool = False


@app.get("/api/series")
def get_series(_=Depends(require_auth)):
    return series.list_series()


@app.post("/api/series")
def post_series(body: SeriesIn, who=Depends(require_auth)):
    if body.music_file and who == "n8n":
        raise HTTPException(403, "müzik lisansı yalnızca panelden onaylanabilir")
    return series.create_series(body.title, body.kind, body.description, body.source_id, body.brief,
                                body.made_for_kids, body.buffer_size, db.get_setting("language", "tr"),
                                body.narration, body.keep_source_audio, body.music_file, body.music_license_url,
                                body.music_confirmed, who)


@app.post("/api/series/run")
def series_run(_=Depends(require_auth)):
    return series.run_batch()


@app.post("/api/series/{series_id}/next")
def series_next(series_id: str, _=Depends(require_auth)):
    return series.next_episode(series_id)


class ManualEpisode(BaseModel):
    script: dict


@app.post("/api/series/{series_id}/episodes")
def series_manual_episode(series_id: str, body: ManualEpisode, who=Depends(require_auth)):
    return series.add_manual_episode(series_id, body.script, who)


class StatusIn(BaseModel):
    status: str


@app.post("/api/series/{series_id}/status")
def series_status(series_id: str, body: StatusIn, _=Depends(require_auth)):
    series.set_status(series_id, body.status)
    return {"ok": True}


class PlaylistIn(BaseModel):
    playlist_id: str


@app.post("/api/series/{series_id}/playlist")
def series_playlist(series_id: str, body: PlaylistIn, _=Depends(require_auth)):
    return series.set_playlist(series_id, body.playlist_id)


# ------------------------------------------------------------------ videolar ve adımlar
@app.get("/api/videos")
def list_videos(stage: str | None = None, _=Depends(require_auth)):
    q = ("SELECT v.id, v.topic, v.stage, v.publish_status, v.duration_sec, v.cost_usd, v.qc_passed, v.youtube_url, "
         "v.scheduled_at, v.published_at, v.last_error, v.created_at, s.title, v.episode_number, "
         "sr.title AS series_title FROM videos v JOIN scripts s ON s.id=v.script_id "
         "LEFT JOIN series sr ON sr.id=v.series_id")
    return db.all_(q + (" WHERE v.stage=%s" if stage else "") + " ORDER BY v.created_at DESC LIMIT 200",
                   (stage,) if stage else None)


@app.get("/api/videos/{video_id}")
def video_detail(video_id: str, _=Depends(require_auth)):
    v = pipeline.get_video(video_id)
    v["assets"] = db.all_("SELECT a.id, a.kind, a.scene_index, a.provider, a.status, a.file_path, a.duration_sec, "
                          "a.cost_usd, a.attempts, a.error, l.kind license_kind, l.license_name, l.verified "
                          "FROM media_assets a LEFT JOIN licenses l ON l.id=a.license_id WHERE a.video_id=%s "
                          "ORDER BY a.kind, a.scene_index", (video_id,))
    v["youtube_metadata"] = pipeline.youtube_metadata(v)
    v["publishing_jobs"] = db.all_("SELECT * FROM publishing_jobs WHERE video_id=%s ORDER BY created_at", (video_id,))
    return v


_STEP = {"media": pipeline.generate_media, "poll-media": pipeline.poll_media, "voice": pipeline.generate_voice,
         "render": pipeline.start_render, "render-status": pipeline.check_render, "qc": pipeline.run_qc}


@app.post("/api/videos/{video_id}/{step}")
def video_step(video_id: str, step: str, request: Request, who=Depends(require_auth)):
    if step in _STEP:
        return _STEP[step](video_id)
    if step in ("approve", "reject"):
        if who == "n8n" and db.get_setting("require_manual_approval", True):
            raise HTTPException(403, "manuel onay zorunlu: onay/ret yalnızca panelden yapılır")
        if step == "approve":
            return pipeline.approve(video_id, who)
        return pipeline.reject(video_id, who, request.query_params.get("reason", ""))
    raise HTTPException(404, "bilinmeyen adım")


@app.post("/api/steps/{step}/run")
def step_batch(step: str, limit: int = 3, _=Depends(require_auth)):
    if step not in pipeline.STEP_STAGES:
        raise HTTPException(404, f"adımlar: {sorted(pipeline.STEP_STAGES)}")
    return pipeline.run_step_batch(step, min(limit, 10))


# ------------------------------------------------------------------ yayın
@app.post("/api/publish/claim")
def publish_claim(_=Depends(require_auth)):
    job = pipeline.claim_next_publish()
    return job or {"job_id": None, "message": "yüklenecek onaylı video yok"}


class PublishResult(BaseModel):
    success: bool
    youtube_video_id: str | None = None
    response: dict | None = None
    error: str | None = None


@app.post("/api/publish/{job_id}/result")
def publish_result(job_id: str, body: PublishResult, _=Depends(require_auth)):
    return pipeline.publish_result(job_id, body.success, body.youtube_video_id, body.response, body.error)


# ------------------------------------------------------------------ analitik / optimizasyon / izleme
@app.get("/api/analytics/targets")
def analytics_targets(_=Depends(require_auth)):
    t = analytics.targets()
    return {"targets": t, "ids_csv": ",".join(x["youtube_video_id"] for x in t),
            "start_date": min((x["published_at"] for x in t if x["published_at"]), default=None)}


class Ingest(BaseModel):
    kind: str
    payload: dict


@app.post("/api/analytics/ingest")
def analytics_ingest(body: Ingest, _=Depends(require_auth)):
    if body.kind == "data_api":
        return {"stored": analytics.ingest_data_api(body.payload)}
    if body.kind == "analytics_api":
        return {"stored": analytics.ingest_analytics_api(body.payload)}
    raise HTTPException(422, "kind: data_api | analytics_api")


@app.get("/api/analytics/performance")
def analytics_perf(_=Depends(require_auth)):
    return analytics.performance_table()


@app.post("/api/optimizer/run")
def optimizer(_=Depends(require_auth)):
    return analytics.optimize()


@app.get("/api/monitor")
def monitor(_=Depends(require_auth)):
    return analytics.monitor()


class ErrorIn(BaseModel):
    source: str
    message: str
    video_id: str | None = None
    detail: dict = {}


@app.post("/api/errors")
def post_error(body: ErrorIn, _=Depends(require_auth)):
    db.log_error(body.source, body.message, body.video_id, body.detail)
    return {"ok": True}


@app.get("/api/errors")
def get_errors(_=Depends(require_auth)):
    return db.all_("SELECT * FROM error_logs ORDER BY created_at DESC LIMIT 200")


@app.post("/api/errors/{error_id}/resolve")
def resolve_error(error_id: int, _=Depends(require_auth)):
    db.exec_("UPDATE error_logs SET resolved=true WHERE id=%s", (error_id,))
    return {"ok": True}


class RunIn(BaseModel):
    workflow: str
    status: str
    execution_id: str | None = None
    video_id: str | None = None
    detail: dict = {}


@app.post("/api/workflow-runs")
def post_run(body: RunIn, _=Depends(require_auth)):
    db.log_run(body.workflow, body.status, body.video_id, body.execution_id, body.detail)
    return {"ok": True}


@app.get("/api/costs")
def costs(_=Depends(require_auth)):
    return {"by_day": db.all_("SELECT date_trunc('day', created_at)::date AS day, provider, sum(cost_usd) cost, "
                              "count(*) calls, sum(CASE WHEN success THEN 0 ELSE 1 END) failures FROM api_usage "
                              "WHERE created_at > now() - interval '30 days' GROUP BY 1,2 ORDER BY 1 DESC, 2"),
            "today": budget.spent_today(), "limit": db.get_setting("daily_cost_limit_usd")}


@app.get("/api/runs")
def runs(_=Depends(require_auth)):
    return db.all_("SELECT * FROM workflow_runs ORDER BY created_at DESC LIMIT 100")


# ------------------------------------------------------------------ panel
@app.get("/", response_class=HTMLResponse)
def panel(request: Request):
    if not request.session.get("user"):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "panel.html", {"user": request.session["user"],
                                                              "n8n_url": config.N8N_PUBLIC_URL})


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
async def login(request: Request):
    ip = request.client.host if request.client else "?"
    now = time.time()
    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < 900]
    if len(_login_attempts[ip]) >= 10:
        return templates.TemplateResponse(request, "login.html", {"error": "Çok fazla deneme. 15 dk bekleyin."},
                                          status_code=429)
    form = await request.form()
    user, pw = str(form.get("username", "")), str(form.get("password", ""))
    ok = (config.PANEL_PASSWORD_HASH and hmac.compare_digest(user, config.PANEL_USER)
          and bcrypt.checkpw(pw.encode(), config.PANEL_PASSWORD_HASH.encode()))
    if not ok:
        _login_attempts[ip].append(now)
        return templates.TemplateResponse(request, "login.html", {"error": "Hatalı kullanıcı adı veya şifre"},
                                          status_code=401)
    request.session.clear()
    request.session["user"] = user
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/media/{path:path}")
def media(path: str, request: Request):
    if not request.session.get("user"):
        raise HTTPException(401)
    p = (config.MEDIA_ROOT / path).resolve()
    if config.MEDIA_ROOT not in p.parents or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)
