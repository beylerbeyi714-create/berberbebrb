#!/usr/bin/env python3
"""n8n iş akışı JSON'larını üretir (n8n 2.45 düğüm sürümleriyle).

    python3 n8n/build_workflows.py                 -> n8n/workflows/*.json
    python3 n8n/build_workflows.py --credentials   -> stdout (.env'den; n8n_bootstrap.sh konteynere aktarır)

İş akışları ince bir orkestrasyon katmanıdır: zamanlama, koşul, bekleme/yoklama,
tekrar deneme ve YouTube OAuth çağrıları n8n'de; ağır iş factory-api ve
video-worker'da yapılır.
"""
import json
import sys
import uuid
from pathlib import Path

OUT = Path(__file__).parent / "workflows"
API = "http://factory-api:8000"
FACTORY_CRED = {"httpHeaderAuth": {"id": "asfFactoryKey001", "name": "Factory API Key"}}
WEBHOOK_CRED = {"httpHeaderAuth": {"id": "asfWebhookKey001", "name": "n8n Webhook Key"}}
YT_CRED = {"youTubeOAuth2Api": {"id": "asfYouTubeOAu001", "name": "YouTube OAuth2 (AI Shorts Factory)"}}
# youtube.force-ssl: seri oynatma listeleri (playlists / playlistItems) için gerekli
YT_SCOPES = ("https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly "
             "https://www.googleapis.com/auth/yt-analytics.readonly https://www.googleapis.com/auth/youtube.force-ssl")
ERROR_WF_ID = "asfWf10ErrorMon0"


class WF:
    def __init__(self, wid: str, name: str, error_wf: bool = True):
        self.wid, self.name, self.nodes, self.conn = wid, name, [], {}
        self.error_wf = error_wf
        self._x = 0

    def node(self, name, type_, version, params, creds=None, pos=None, **extra):
        self._x += 1
        n = {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.wid}/{name}")), "name": name,
             "type": f"n8n-nodes-base.{type_}", "typeVersion": version,
             "position": pos or [self._x * 240, 300], "parameters": params, **extra}
        if creds:
            n["credentials"] = creds
        self.nodes.append(n)
        return name

    def link(self, a, b, out=0):
        outs = self.conn.setdefault(a, {"main": []})["main"]
        while len(outs) <= out:
            outs.append([])
        outs[out].append({"node": b, "type": "main", "index": 0})

    def chain(self, *names):
        for a, b in zip(names, names[1:]):
            self.link(a, b)

    def dump(self):
        settings = {"executionOrder": "v1", "saveManualExecutions": True, "timezone": "Europe/Istanbul"}
        if self.error_wf:
            settings["errorWorkflow"] = ERROR_WF_ID
        return {"id": self.wid, "name": self.name, "nodes": self.nodes, "connections": self.conn,
                "settings": settings, "active": False, "pinData": {}, "tags": [],
                "versionId": str(uuid.uuid5(uuid.NAMESPACE_URL, self.wid + "/v1"))}


# ------------------------------------------------------------------ düğüm yardımcıları
def api(method, path, body=None, query=None, timeout=180000, never_error=False):
    p = {"method": method, "url": f"={API}{path}" if "{{" in path else f"{API}{path}",
         "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
         "options": {"timeout": timeout}}
    if body is not None:
        p.update(sendBody=True, specifyBody="json", jsonBody=body)
    if query:
        p.update(sendQuery=True, queryParameters={"parameters": [{"name": k, "value": v} for k, v in query.items()]})
    if never_error:
        p["options"]["response"] = {"response": {"fullResponse": True, "neverError": True}}
    return p


def schedule(rule):
    return {"rule": {"interval": [rule]}}


def cond_bool(expr):
    return {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose",
                                       "version": 2},
                           "conditions": [{"id": str(uuid.uuid5(uuid.NAMESPACE_URL, expr)), "leftValue": expr, "rightValue": "",
                                           "operator": {"type": "boolean", "operation": "true",
                                                        "singleValue": True}}],
                           "combinator": "and"}, "options": {}, "looseTypeValidation": True}


def webhook(path):
    return {"httpMethod": "POST", "path": path, "authentication": "headerAuth", "responseMode": "lastNode",
            "options": {}}


def log_run(wf: WF, after: str, workflow_key: str, status_expr="success"):
    n = wf.node("Log run", "httpRequest", 4.2, api(
        "POST", "/api/workflow-runs",
        body='={{ JSON.stringify({ workflow: "' + workflow_key + '", status: "' + status_expr +
             '", execution_id: $execution.id, detail: $json }) }}'), FACTORY_CRED)
    wf.link(after, n)
    return n


def gated_step(wid, name, key, gate, rule, method, path, query=None, body="={}"):
    """Zamanlayıcı -> otomasyon açık mı? -> adım -> kayıt. Webhook ile elle tetikleme kapıyı atlar."""
    wf = WF(wid, name)
    s = wf.node("Schedule", "scheduleTrigger", 1.2, schedule(rule), pos=[0, 200])
    w = wf.node("Manual webhook", "webhook", 2, webhook(f"asf-{key}"), WEBHOOK_CRED, pos=[0, 420],
                webhookId=str(uuid.uuid5(uuid.NAMESPACE_URL, wid + "/hook")))
    g = wf.node("Should run?", "httpRequest", 4.2, api("GET", "/api/should-run", query={"workflow": gate}),
                FACTORY_CRED, pos=[240, 200])
    i = wf.node("Automation on?", "if", 2.2, cond_bool("={{ $json.run }}"), pos=[480, 200])
    st = wf.node(f"Run: {key}", "httpRequest", 4.2, api(method, path, body=body, query=query), FACTORY_CRED,
                 pos=[720, 300])
    wf.chain(s, g, i)
    wf.link(i, st, 0)
    wf.link(w, st)
    log_run(wf, st, key)
    return wf


# ------------------------------------------------------------------ 01-06, 09: adım akışları
def wf_simple():
    return [
        gated_step("asfWf01Research0", "01 Daily Content Research", "research", "research",
                   {"field": "cronExpression", "expression": "10 7 * * *"}, "POST", "/api/research/run",
                   query={"count": "10"}),
        gated_step("asfWf02ScriptGen", "02 Script Generator", "script", "script",
                   {"field": "hours", "hoursInterval": 2}, "POST", "/api/scripts/generate"),
        gated_step("asfWf03MediaGen0", "03 AI Media Generator", "media", "media",
                   {"field": "minutes", "minutesInterval": 5}, "POST", "/api/steps/media/run", query={"limit": "3"}),
        gated_step("asfWf04VoiceSubs", "04 Voice And Subtitles", "voice", "voice",
                   {"field": "minutes", "minutesInterval": 5}, "POST", "/api/steps/voice/run", query={"limit": "3"}),
        gated_step("asfWf05Renderer0", "05 Video Renderer", "render", "render",
                   {"field": "minutes", "minutesInterval": 3}, "POST", "/api/steps/render/run", query={"limit": "3"}),
        gated_step("asfWf06QualityCt", "06 Quality Control", "qc", "qc",
                   {"field": "minutes", "minutesInterval": 5}, "POST", "/api/steps/qc/run", query={"limit": "5"}),
        gated_step("asfWf09Optimizer", "09 Performance Optimizer", "optimizer", "optimizer",
                   {"field": "cronExpression", "expression": "40 4 * * 1"}, "POST", "/api/optimizer/run"),
        gated_step("asfWf11SeriesPln", "11 Series Episode Planner", "series", "series",
                   {"field": "hours", "hoursInterval": 1}, "POST", "/api/series/run"),
    ]


# ------------------------------------------------------------------ 07: YouTube yayıncı
def wf_publisher():
    wf = WF("asfWf07YTPublish", "07 YouTube Publisher")
    s = wf.node("Schedule", "scheduleTrigger", 1.2, schedule({"field": "minutes", "minutesInterval": 30}), pos=[0, 200])
    w = wf.node("Manual webhook", "webhook", 2, webhook("asf-publish"), WEBHOOK_CRED, pos=[0, 440],
                webhookId=str(uuid.uuid5(uuid.NAMESPACE_URL, "asfWf07/hook")))
    g = wf.node("Should run?", "httpRequest", 4.2, api("GET", "/api/should-run", query={"workflow": "publish"}),
                FACTORY_CRED, pos=[220, 200])
    i = wf.node("Automation on?", "if", 2.2, cond_bool("={{ $json.run }}"), pos=[440, 200])
    # YouTube bağlantısı yoksa video kilitlenmeden durulur (kimlik hatası da normal çıktıya düşer)
    yc = wf.node("YouTube connected?", "httpRequest", 4.2, {
        "method": "GET", "url": "https://www.googleapis.com/youtube/v3/channels",
        "authentication": "predefinedCredentialType", "nodeCredentialType": "youTubeOAuth2Api",
        "sendQuery": True, "queryParameters": {"parameters": [{"name": "part", "value": "id,snippet"},
                                                              {"name": "mine", "value": "true"}]},
        "options": {"response": {"response": {"fullResponse": True, "neverError": True}}, "timeout": 30000}},
        YT_CRED, pos=[440, 440], onError="continueRegularOutput")
    yok = wf.node("Channel OK?", "if", 2.2, cond_bool(
        "={{ $json.statusCode === 200 && ($json.body?.items?.length || 0) > 0 }}"), pos=[660, 440])
    ynot = wf.node("Log: YouTube not connected", "httpRequest", 4.2, api("POST", "/api/errors", body=(
        "={{ JSON.stringify({ source: 'youtube-publisher', message: 'YouTube bağlantısı yok veya yetkisiz: ' + "
        "($json.error?.message || ('HTTP ' + $json.statusCode + ' ' + JSON.stringify($json.body || {}).slice(0, 300))) }) }}")),
        FACTORY_CRED, pos=[880, 560])
    c = wf.node("Claim approved video", "httpRequest", 4.2, api("POST", "/api/publish/claim"), FACTORY_CRED,
                pos=[660, 320])
    has = wf.node("Has job?", "if", 2.2, cond_bool("={{ !!$json.job_id }}"), pos=[880, 320])
    rd = wf.node("Read MP4", "readWriteFile", 1, {
        "operation": "read", "fileSelector": "=/data/{{ $json.file_path }}",
        "options": {"dataPropertyName": "data"}}, pos=[1100, 320])
    init = wf.node("YouTube: start resumable upload", "httpRequest", 4.2, {
        "method": "POST",
        "url": "https://www.googleapis.com/upload/youtube/v3/videos",
        "authentication": "predefinedCredentialType", "nodeCredentialType": "youTubeOAuth2Api",
        "sendQuery": True, "queryParameters": {"parameters": [
            {"name": "uploadType", "value": "resumable"}, {"name": "part", "value": "snippet,status"},
            {"name": "notifySubscribers", "value": "false"}]},
        "sendHeaders": True, "headerParameters": {"parameters": [
            {"name": "X-Upload-Content-Type", "value": "video/mp4"},
            {"name": "X-Upload-Content-Length", "value": "={{ $('Claim approved video').item.json.file_size }}"}]},
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify($('Claim approved video').item.json.metadata) }}",
        "options": {"response": {"response": {"fullResponse": True, "neverError": True}}, "timeout": 60000}},
        YT_CRED, pos=[1320, 320])
    ok1 = wf.node("Session created?", "if", 2.2, cond_bool("={{ $json.statusCode === 200 && !!$json.headers.location }}"),
                  pos=[1540, 320])
    put = wf.node("YouTube: upload bytes", "httpRequest", 4.2, {
        "method": "PUT", "url": "={{ $json.headers.location }}",
        "authentication": "predefinedCredentialType", "nodeCredentialType": "youTubeOAuth2Api",
        "sendHeaders": True, "headerParameters": {"parameters": [{"name": "Content-Type", "value": "video/mp4"}]},
        "sendBody": True, "contentType": "binaryData", "inputDataFieldName": "data",
        "options": {"response": {"response": {"fullResponse": True, "neverError": True}}, "timeout": 1800000}},
        YT_CRED, pos=[1760, 260])
    # PUT düğümü binary veriyi "Read MP4" çıktısından almalı: ifade ile değil, öğe eşleştirmesi için Code ile birleştiririz
    merge = wf.node("Attach binary", "code", 2, {"jsCode": (
        "const loc = $input.first().json.headers.location;\n"
        "const bin = $('Read MP4').first().binary;\n"
        "return [{ json: { headers: { location: loc } }, binary: bin }];")}, pos=[1650, 160])
    res = wf.node("Build result", "code", 2, {"jsCode": (
        "const r = $input.first().json;\n"
        "const job = $('Claim approved video').first().json.job_id;\n"
        "const ok = r.statusCode >= 200 && r.statusCode < 300 && r.body && r.body.id;\n"
        "return [{ json: { job_id: job, success: !!ok, youtube_video_id: ok ? r.body.id : null,\n"
        "  response: (typeof r.body === 'object') ? r.body : { raw: String(r.body).slice(0, 2000) },\n"
        "  error: ok ? null : `HTTP ${r.statusCode}: ${JSON.stringify(r.body).slice(0, 1500)}` } }];")},
        pos=[1980, 320])
    fail = wf.node("Session failed", "code", 2, {"jsCode": (
        "const r = $input.first().json;\n"
        "const job = $('Claim approved video').first().json.job_id;\n"
        "return [{ json: { job_id: job, success: false, youtube_video_id: null, response: r.body || {},\n"
        "  error: `upload session HTTP ${r.statusCode}: ${JSON.stringify(r.body).slice(0, 1500)}` } }];")},
        pos=[1760, 480])
    rep = wf.node("Report to factory", "httpRequest", 4.2, api(
        "POST", "/api/publish/{{ $json.job_id }}/result",
        body="={{ JSON.stringify({ success: $json.success, youtube_video_id: $json.youtube_video_id, "
             "response: $json.response, error: $json.error }) }}"), FACTORY_CRED, pos=[2200, 320])
    wf.chain(s, g, i)
    wf.link(i, yc, 0)
    wf.link(w, yc)
    wf.link(yc, yok)
    wf.link(yok, c, 0)
    wf.link(yok, ynot, 1)
    wf.chain(c, has)
    wf.link(has, rd, 0)
    wf.chain(rd, init, ok1)
    wf.link(ok1, merge, 0)
    wf.link(ok1, fail, 1)
    wf.chain(merge, put, res, rep)
    wf.link(fail, rep)
    log_run(wf, rep, "publish")

    # --- seri bölümüyse: oynatma listesine sırayla ekle (liste yoksa önce oluştur)
    yt = {"authentication": "predefinedCredentialType", "nodeCredentialType": "youTubeOAuth2Api"}
    soft = {"response": {"response": {"fullResponse": True, "neverError": True}}, "timeout": 30000}
    ser = "$('Claim approved video').first().json.series"
    is_ser = wf.node("Series episode?", "if", 2.2, cond_bool(f"={{{{ $json.success && !!{ser} }}}}"), pos=[2200, 120])
    has_pl = wf.node("Has playlist?", "if", 2.2, cond_bool(f"={{{{ !!{ser}.playlist_id }}}}"), pos=[2420, 120])
    mk_pl = wf.node("YouTube: create playlist", "httpRequest", 4.2, {
        "method": "POST", "url": "https://www.googleapis.com/youtube/v3/playlists", **yt,
        "sendQuery": True, "queryParameters": {"parameters": [{"name": "part", "value": "snippet,status"}]},
        "sendBody": True, "specifyBody": "json", "jsonBody": f"={{{{ JSON.stringify({ser}.playlist_body) }}}}",
        "options": soft}, YT_CRED, pos=[2640, 200], onError="continueRegularOutput")
    save_pl = wf.node("Save playlist id", "httpRequest", 4.2, api(
        "POST", f"/api/series/{{{{ {ser}.id }}}}/playlist",
        body="={{ JSON.stringify({ playlist_id: $json.body.id }) }}"), FACTORY_CRED, pos=[2860, 200])

    def add_item(name, playlist_expr, pos):
        return wf.node(name, "httpRequest", 4.2, {
            "method": "POST", "url": "https://www.googleapis.com/youtube/v3/playlistItems", **yt,
            "sendQuery": True, "queryParameters": {"parameters": [{"name": "part", "value": "snippet"}]},
            "sendBody": True, "specifyBody": "json",
            "jsonBody": ("={{ JSON.stringify({ snippet: { playlistId: " + playlist_expr + ", resourceId: "
                         "{ kind: 'youtube#video', videoId: $('Build result').first().json.youtube_video_id } } }) }}"),
            "options": soft}, YT_CRED, pos=pos, onError="continueRegularOutput")

    add_old = add_item("Add to existing playlist", f"{ser}.playlist_id", [2640, 40])
    add_new = add_item("Add to new playlist", "$json.playlist_id", [3080, 200])
    pl_ok = wf.node("Playlist OK?", "if", 2.2, cond_bool("={{ $json.statusCode >= 200 && $json.statusCode < 300 }}"),
                    pos=[3300, 120])
    pl_err = wf.node("Log playlist error", "httpRequest", 4.2, api("POST", "/api/errors", body=(
        "={{ JSON.stringify({ source: 'youtube-playlist', video_id: $('Claim approved video').first().json.video_id, "
        "message: 'Oynatma listesine eklenemedi (video yüklendi): ' + ($json.error?.message || "
        "('HTTP ' + $json.statusCode + ' ' + JSON.stringify($json.body || {}).slice(0, 300))) }) }}")),
        FACTORY_CRED, pos=[3520, 200])
    wf.link(res, is_ser)
    wf.link(is_ser, has_pl, 0)
    wf.link(has_pl, add_old, 0)
    wf.link(has_pl, mk_pl, 1)
    wf.chain(mk_pl, save_pl, add_new)
    wf.link(add_old, pl_ok)
    wf.link(add_new, pl_ok)
    wf.link(pl_ok, pl_err, 1)
    return wf


# ------------------------------------------------------------------ 08: analitik
def wf_analytics():
    wf = WF("asfWf08Analytics", "08 Analytics Collector")
    s = wf.node("Schedule", "scheduleTrigger", 1.2, schedule({"field": "cronExpression", "expression": "20 3 * * *"}),
                pos=[0, 200])
    w = wf.node("Manual webhook", "webhook", 2, webhook("asf-analytics"), WEBHOOK_CRED, pos=[0, 420],
                webhookId=str(uuid.uuid5(uuid.NAMESPACE_URL, "asfWf08/hook")))
    t = wf.node("Get targets", "httpRequest", 4.2, api("GET", "/api/analytics/targets"), FACTORY_CRED, pos=[240, 300])
    has = wf.node("Any videos?", "if", 2.2, cond_bool("={{ $json.targets.length > 0 }}"), pos=[460, 300])
    chunks = wf.node("Chunk ids (50)", "code", 2, {"jsCode": (
        "const ids = $input.first().json.targets.map(t => t.youtube_video_id);\n"
        "const out = [];\nfor (let i = 0; i < ids.length; i += 50) out.push({ json: { ids: ids.slice(i, i + 50).join(',') } });\n"
        "return out;")}, pos=[680, 200])
    yt = {"authentication": "predefinedCredentialType", "nodeCredentialType": "youTubeOAuth2Api"}
    stats = wf.node("Data API: videos.list statistics", "httpRequest", 4.2, {
        "method": "GET", "url": "https://www.googleapis.com/youtube/v3/videos", **yt,
        "sendQuery": True, "queryParameters": {"parameters": [{"name": "part", "value": "statistics"},
                                                              {"name": "id", "value": "={{ $json.ids }}"}]},
        "options": {}}, YT_CRED, pos=[900, 200])
    ing1 = wf.node("Ingest Data API", "httpRequest", 4.2, api(
        "POST", "/api/analytics/ingest", body="={{ JSON.stringify({ kind: 'data_api', payload: $json }) }}"),
        FACTORY_CRED, pos=[1120, 200])
    q = lambda **kw: {"sendQuery": True, "queryParameters": {"parameters": [{"name": k, "value": v} for k, v in kw.items()]}}
    rng = {"ids": "channel==MINE",
           "startDate": "={{ $('Get targets').first().json.start_date }}",
           "endDate": "={{ $now.toFormat('yyyy-MM-dd') }}"}
    top = wf.node("Analytics API: per-video totals", "httpRequest", 4.2, {
        "method": "GET", "url": "https://youtubeanalytics.googleapis.com/v2/reports", **yt,
        **q(**rng, metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,"
                           "subscribersGained,subscribersLost", dimensions="video", sort="-views", maxResults="200"),
        "options": {}}, YT_CRED, pos=[680, 420])
    per = wf.node("Latest 20 videos", "code", 2, {"jsCode": (
        "return $('Get targets').first().json.targets.slice(0, 20).map(t => ({ json: t }));")}, pos=[900, 420])
    trf = wf.node("Analytics API: traffic sources", "httpRequest", 4.2, {
        "method": "GET", "url": "https://youtubeanalytics.googleapis.com/v2/reports", **yt,
        **q(**rng, metrics="views", dimensions="insightTrafficSourceType", filters="=video=={{ $json.youtube_video_id }}"),
        "options": {}}, YT_CRED, pos=[1120, 420])
    ret = wf.node("Analytics API: retention", "httpRequest", 4.2, {
        "method": "GET", "url": "https://youtubeanalytics.googleapis.com/v2/reports", **yt,
        **q(**rng, metrics="audienceWatchRatio,relativeRetentionPerformance", dimensions="elapsedVideoTimeRatio",
            filters="=video=={{ $('Latest 20 videos').item.json.youtube_video_id }}"),
        "options": {}}, YT_CRED, pos=[1340, 420])
    agg = wf.node("Aggregate", "code", 2, {"jsCode": (
        "const vids = $('Latest 20 videos').all().map(i => i.json.youtube_video_id);\n"
        "const traffic = {}, retention = {};\n"
        "$('Analytics API: traffic sources').all().forEach((it, i) => traffic[vids[i]] = it.json);\n"
        "$input.all().forEach((it, i) => retention[vids[i]] = it.json);\n"
        "return [{ json: { kind: 'analytics_api', payload: { daily: $('Analytics API: per-video totals').first().json, traffic, retention } } }];")},
        pos=[1560, 420])
    ing2 = wf.node("Ingest Analytics API", "httpRequest", 4.2, api(
        "POST", "/api/analytics/ingest", body="={{ JSON.stringify($json) }}"), FACTORY_CRED, pos=[1780, 420])
    wf.link(s, t)
    wf.link(w, t)
    wf.chain(t, has)
    wf.link(has, chunks, 0)
    wf.link(has, top, 0)
    wf.chain(chunks, stats, ing1)
    wf.chain(top, per, trf, ret, agg, ing2)
    log_run(wf, ing2, "analytics")
    return wf


# ------------------------------------------------------------------ 10: hata izleme
def wf_errors():
    wf = WF(ERROR_WF_ID, "10 Error Monitoring", error_wf=False)
    et = wf.node("Error Trigger", "errorTrigger", 1, {}, pos=[0, 200])
    pe = wf.node("Log workflow error", "httpRequest", 4.2, api("POST", "/api/errors", body=(
        "={{ JSON.stringify({ source: 'n8n:' + ($json.workflow?.name || 'unknown'), "
        "message: ($json.execution?.error?.message || 'workflow error').slice(0, 1000), "
        "detail: { node: $json.execution?.lastNodeExecuted, description: ($json.execution?.error?.description || '').slice(0, 1500), execution_id: $json.execution?.id, "
        "url: $json.execution?.url, retry_of: $json.execution?.retryOf } }) }}")), FACTORY_CRED, pos=[240, 200])
    s = wf.node("Hourly", "scheduleTrigger", 1.2, schedule({"field": "hours", "hoursInterval": 1}), pos=[0, 440])
    m = wf.node("Monitor", "httpRequest", 4.2, api("GET", "/api/monitor"), FACTORY_CRED, pos=[240, 440])
    has = wf.node("Alerts?", "if", 2.2, cond_bool("={{ $json.alerts.length > 0 }}"), pos=[460, 440])
    pa = wf.node("Log alerts", "httpRequest", 4.2, api("POST", "/api/errors", body=(
        "={{ JSON.stringify({ source: 'monitor', message: $json.alerts.join(' | '), "
        "detail: { cost_today_usd: $json.cost_today_usd, uploads_today: $json.uploads_today } }) }}")),
        FACTORY_CRED, pos=[680, 440])
    wf.chain(et, pe)
    wf.chain(s, m, has)
    wf.link(has, pa, 0)
    return wf


# ------------------------------------------------------------------ 00: uçtan uca test zinciri
def poll_loop(wf: WF, name: str, path: str, done_stage: str, x: int, y: int, wait_s: int, max_iter: int):
    """POST path -> stage kontrol -> (tamam) çık / (değil) bekle -> tekrar. max_iter aşılırsa hata."""
    p = wf.node(name, "httpRequest", 4.2, api("POST", path), FACTORY_CRED, pos=[x, y])
    chk = wf.node(f"{name} done?", "code", 2, {"jsCode": (
        f"const s = $input.first().json.stage;\n"
        f"if (s === 'failed') throw new Error('{name}: video failed — ' + JSON.stringify($input.first().json).slice(0, 500));\n"
        f"if ($runIndex >= {max_iter}) throw new Error('{name}: zaman aşımı');\n"
        f"return [{{ json: {{ done: s === '{done_stage}', stage: s }} }}];")}, pos=[x + 220, y])
    i = wf.node(f"{name} ready?", "if", 2.2, cond_bool("={{ $json.done }}"), pos=[x + 440, y])
    wt = wf.node(f"{name} wait", "wait", 1.1, {"amount": wait_s, "unit": "seconds"}, pos=[x + 440, y + 180],
                 webhookId=str(uuid.uuid5(uuid.NAMESPACE_URL, wf.wid + name)))
    wf.chain(p, chk, i)
    wf.link(i, wt, 1)
    wf.link(wt, p)
    return p, i


def wf_e2e():
    wf = WF("asfWf00E2EPipeln", "00 E2E Pipeline (test)")
    vid = "{{ $('Context').first().json.video_id }}"
    w = wf.node("Webhook", "webhook", 2, webhook("asf-e2e"), WEBHOOK_CRED, pos=[0, 300],
                webhookId=str(uuid.uuid5(uuid.NAMESPACE_URL, "asfWf00/hook")))
    has_vid = wf.node("Has video_id?", "if", 2.2, cond_bool("={{ !!$json.body.video_id }}"), pos=[220, 300])
    has_script = wf.node("Has script?", "if", 2.2, cond_bool("={{ !!$json.body.script }}"), pos=[440, 420])
    manual = wf.node("Save manual script", "httpRequest", 4.2, api(
        "POST", "/api/scripts", body="={{ JSON.stringify({ topic: $json.body.topic, script: $json.body.script }) }}"),
        FACTORY_CRED, pos=[660, 360])
    gen = wf.node("Generate script (LLM)", "httpRequest", 4.2, api("POST", "/api/scripts/generate", body="={}"),
                  FACTORY_CRED, pos=[660, 520])
    ctx = wf.node("Context", "code", 2, {"jsCode": (
        "const j = $input.first().json;\n"
        "const id = j.video_id || (j.body && j.body.video_id);\n"
        "if (!id) throw new Error('video_id yok: ' + JSON.stringify(j).slice(0, 300));\n"
        "return [{ json: { video_id: id } }];")}, pos=[900, 300])
    wf.link(w, has_vid)
    wf.link(has_vid, ctx, 0)
    wf.link(has_vid, has_script, 1)
    wf.link(has_script, manual, 0)
    wf.link(has_script, gen, 1)
    wf.link(manual, ctx)
    wf.link(gen, ctx)
    media = wf.node("Generate media", "httpRequest", 4.2, api("POST", f"/api/videos/{vid}/media"), FACTORY_CRED,
                    pos=[1120, 300])
    wf.link(ctx, media)
    pm, pm_ok = poll_loop(wf, "Poll media", f"/api/videos/{vid}/poll-media", "media_ready", 1340, 300, 15, 80)
    wf.link(media, pm)
    voice = wf.node("Voice + subtitles", "httpRequest", 4.2, api("POST", f"/api/videos/{vid}/voice"), FACTORY_CRED,
                    pos=[2000, 300])
    wf.link(pm_ok, voice, 0)
    render = wf.node("Start render", "httpRequest", 4.2, api("POST", f"/api/videos/{vid}/render"), FACTORY_CRED,
                     pos=[2220, 300])
    wf.link(voice, render)
    rs, rs_ok = poll_loop(wf, "Render status", f"/api/videos/{vid}/render-status", "rendered", 2440, 300, 10, 120)
    wf.link(render, rs)
    qc = wf.node("Quality control", "httpRequest", 4.2, api("POST", f"/api/videos/{vid}/qc"), FACTORY_CRED,
                 pos=[3100, 300])
    wf.link(rs_ok, qc, 0)
    summary = wf.node("Summary", "code", 2, {"jsCode": (
        "const q = $('Quality control').first().json;\n"
        "return [{ json: { video_id: q.video_id, qc_passed: q.passed, public_eligible: q.public_eligible,\n"
        "  failed_checks: Object.entries(q.checks || {}).filter(([k, c]) => !c.ok).map(([k, c]) => `${k}: ${c.detail}`),\n"
        "  warnings: q.warnings, next: q.passed ? 'Panelden onaylayın; 07 YouTube Publisher gizli olarak yükler.' : 'QC başarısız: yayınlanmaz.' } }];")},
        pos=[3320, 300])
    wf.link(log_run(wf, qc, "e2e"), summary)   # yanıt = son düğüm (Summary)
    return wf


def credentials(with_youtube: bool):
    env = {}
    for ln in (Path(__file__).parent.parent / ".env").read_text().splitlines():
        if "=" in ln and not ln.startswith("#"):
            k, v = ln.split("=", 1)
            env[k] = v.strip("'\"")
    creds = [
        {"id": "asfFactoryKey001", "name": "Factory API Key", "type": "httpHeaderAuth",
         "data": {"name": "X-API-Key", "value": env["FACTORY_API_TOKEN"]}},
        {"id": "asfWebhookKey001", "name": "n8n Webhook Key", "type": "httpHeaderAuth",
         "data": {"name": "X-API-Key", "value": env["N8N_WEBHOOK_TOKEN"]}},
    ]
    if not with_youtube:   # var olan YouTube kimliğinin (OAuth token'ları) üzerine yazma
        return creds
    return creds + [
        {"id": "asfYouTubeOAu001", "name": "YouTube OAuth2 (AI Shorts Factory)", "type": "youTubeOAuth2Api",
         "data": {"clientId": env.get("GOOGLE_OAUTH_CLIENT_ID", ""), "clientSecret": env.get("GOOGLE_OAUTH_CLIENT_SECRET", ""),
                  "customScopes": True, "enabledScopes": YT_SCOPES}},
    ]


if __name__ == "__main__":
    if "--credentials" in sys.argv:
        # gizli değerler diske yazılmaz; stdout'tan doğrudan n8n konteynerine aktarılır
        print(json.dumps(credentials("--with-youtube" in sys.argv)))
    else:
        OUT.mkdir(exist_ok=True)
        wfs = [wf_e2e(), *wf_simple(), wf_publisher(), wf_analytics(), wf_errors()]
        for wf in wfs:
            fn = OUT / (wf.name.split(" ")[0] + "_" + "_".join(wf.name.split(" ")[1:]).lower()
                        .replace("(", "").replace(")", "") + ".json")
            fn.write_text(json.dumps(wf.dump(), indent=2, ensure_ascii=False))
            print(fn.name, len(wf.nodes), "düğüm")
