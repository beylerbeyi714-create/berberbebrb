"""AI Shorts Factory — video işleme servisi.

FFmpeg ile 9:16 Shorts montajı ve teknik kalite probu yapar.
n8n veya factory-api bu servise HTTP üzerinden iş gönderir; iş arka planda
çalışır, durum GET /jobs/{id} ile izlenir.

Tüm dosya yolları MEDIA_ROOT altında olmak zorundadır (yol kaçışı engellenir).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", "/data")).resolve()
API_TOKEN = os.environ.get("WORKER_API_TOKEN", "")
MAX_PARALLEL = int(os.environ.get("WORKER_MAX_PARALLEL", "1"))
W, H, FPS = 1080, 1920, 30
FONT = os.environ.get("SUBTITLE_FONT", "DejaVu Sans")

app = FastAPI(title="AI Shorts Factory – Video Worker", version="1.0.0")
_jobs: dict[str, dict] = {}
_slots = threading.BoundedSemaphore(MAX_PARALLEL)


def auth(x_api_key: str = Header(default="")):
    if not API_TOKEN or x_api_key != API_TOKEN:
        raise HTTPException(401, "invalid api key")


def safe_path(p: str, must_exist: bool = True) -> Path:
    path = (MEDIA_ROOT / p).resolve() if not os.path.isabs(p) else Path(p).resolve()
    if MEDIA_ROOT not in path.parents and path != MEDIA_ROOT:
        raise HTTPException(400, f"path outside media root: {p}")
    if must_exist and not path.exists():
        raise HTTPException(400, f"file not found: {p}")
    return path


def run(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed ({proc.returncode}): {proc.stderr[-1500:]}")
    return proc


def probe(path: Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", str(path)], timeout=60).stdout
    return json.loads(out)


# ------------------------------------------------------------------ modeller
class Scene(BaseModel):
    image_path: str | None = None       # durağan görsel (Ken Burns uygulanır)
    clip_path: str | None = None        # video klip
    clip_start: float = Field(default=0.0, ge=0)   # klibin kaçıncı saniyesinden başlanacak (kaynak film kesiti)
    fit: str = "crop"                   # "crop": 9:16'ya kırp · "blur": kırpmadan sığdır, kenarları bulanık doldur
    keep_audio: bool = False            # klibin kendi sesini kullan (seslendirme yoksa)
    is_source: bool = False             # kaynak film kesiti: [clip_start, clip_start+duration] dışına asla taşmaz
    still: bool = False                 # görseli hareketsiz göster (bölüm kartları: yazı kaymasın)
    voice_path: str | None = None
    duration: float = Field(gt=0.3, le=60)


class RenderRequest(BaseModel):
    video_id: str
    scenes: list[Scene] = Field(min_length=1, max_length=30)
    subtitle_path: str | None = None    # .srt — videoya gömülür
    music_path: str | None = None
    music_volume: float = 0.12
    transition: str = "fadeblack"       # xfade geçişi (kısa karartma: iki görüntü üst üste binmez); "none" kapatır
    transition_sec: float = 0.35
    output_path: str


class ProbeRequest(BaseModel):
    path: str
    scene_midpoints: list[float] = []   # tekrar eden sahne kontrolü için


# ------------------------------------------------------------------ montaj
def _scene_segment(scene: Scene, length: float, out: Path, idx: int) -> None:
    """Tek sahneyi 1080x1920, 30fps, sessiz bir H.264 parçasına dönüştürür."""
    frames = max(1, round(length * FPS))
    if scene.clip_path:
        src = safe_path(scene.clip_path)
        if scene.fit == "blur":
            # kırpmadan sığdır: arka plan = aynı görüntünün büyütülüp bulanıklaştırılmış hali
            vf = (f"split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                  f"boxblur=24:4,eq=brightness=-0.08[bg];[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
                  f"[bg][fg]overlay=(W-w)/2:(H-h)/2,fps={FPS},setsar=1,format=yuv420p")
        else:
            vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                  f"fps={FPS},setsar=1,format=yuv420p")
        if scene.is_source:
            # kaynak film kesiti: yalnızca kendi aralığı okunur; geçiş payı son kare dondurularak eklenir
            # (bir sonraki bölümün görüntüsü bu bölüme asla sızmaz)
            pad = max(0.0, length - scene.duration)
            vf += f",tpad=stop_mode=clone:stop_duration={pad + 0.1:.3f}"
            cmd = ["ffmpeg", "-y", "-ss", f"{scene.clip_start:.3f}", "-t", f"{scene.duration:.3f}",
                   "-i", str(src), "-t", f"{length:.3f}", "-vf", vf, "-an"]
        else:
            # kısa üretim klipleri (yapay zekâ videoları) sahne süresine göre döngülenir
            cmd = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(src), "-t", f"{length:.3f}", "-vf", vf, "-an"]
    elif scene.image_path and scene.still:
        src = safe_path(scene.image_path)
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"fps={FPS},setsar=1,format=yuv420p")
        cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(src), "-frames:v", str(frames), "-vf", vf]
    elif scene.image_path:
        src = safe_path(scene.image_path)
        # Ken Burns: sahne sırasına göre yakınlaşma / uzaklaşma dönüşümlü
        zoom = "min(1.0+0.0009*on,1.15)" if idx % 2 == 0 else "max(1.15-0.0009*on,1.0)"
        vf = (f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2},"
              f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},"
              f"setsar=1,format=yuv420p")
        cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(src), "-frames:v", str(frames), "-vf", vf]
    else:
        raise ValueError(f"scene {idx}: image_path or clip_path required")
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(FPS), str(out)]
    run(cmd)


def _has_audio(path: Path) -> bool:
    return any(st["codec_type"] == "audio" for st in probe(path)["streams"])


def _srt_style() -> str:
    return (f"FontName={FONT},FontSize=13,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
            "BackColour=&H80000000,BorderStyle=1,Outline=2,Shadow=0,Bold=1,"
            "Alignment=2,MarginV=70")


def render(job_id: str, req: RenderRequest) -> None:
    job = _jobs[job_id]
    with _slots:
        job.update(status="processing", started_at=time.time())
        tmp = Path(tempfile.mkdtemp(prefix="render_", dir=str(MEDIA_ROOT / "tmp")))
        try:
            out_path = safe_path(req.output_path, must_exist=False)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            n = len(req.scenes)
            t = req.transition_sec if (req.transition != "none" and n > 1) else 0.0

            # 1) sahne parçaları: geçiş süresi kadar uzatılır ki toplam süre korunsun
            segs = []
            for i, sc in enumerate(req.scenes):
                length = sc.duration + (t if i < n - 1 else 0.0)
                seg = tmp / f"seg_{i:02d}.mp4"
                _scene_segment(sc, length, seg, i)
                segs.append((seg, length))
                job["progress"] = round(0.6 * (i + 1) / n, 2)

            # 2) xfade zinciri
            inputs: list[str] = []
            for seg, _ in segs:
                inputs += ["-i", str(seg)]
            if n == 1 or t == 0:
                fc = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
            else:
                parts, prev, cum = [], "[0:v]", segs[0][1]
                for i in range(1, n):
                    offset = cum - t
                    label = f"[x{i}]" if i < n - 1 else "[v]"
                    parts.append(f"{prev}[{i}:v]xfade=transition={req.transition}:"
                                 f"duration={t:.3f}:offset={offset:.3f}{label}")
                    prev, cum = label, cum + segs[i][1] - t
                fc = ";".join(parts)
            visual = tmp / "visual.mp4"
            run(["ffmpeg", "-y", *inputs, "-filter_complex", fc, "-map", "[v]",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                 str(visual)])
            job["progress"] = 0.75

            # 3) ses: her sahnenin seslendirmesi sahne süresine pad edilir, sonra birleştirilir
            total = sum(sc.duration for sc in req.scenes)
            a_inputs, a_parts = [], []
            for i, sc in enumerate(req.scenes):
                if sc.voice_path:
                    a_inputs += ["-i", str(safe_path(sc.voice_path))]
                elif sc.keep_audio and sc.clip_path and _has_audio(safe_path(sc.clip_path)):
                    # kaynak filmin o kesitteki orijinal sesi
                    a_inputs += ["-ss", f"{sc.clip_start:.3f}", "-t", f"{sc.duration:.3f}",
                                 "-i", str(safe_path(sc.clip_path))]
                else:
                    a_inputs += ["-f", "lavfi", "-t", f"{sc.duration:.3f}", "-i",
                                 "anullsrc=r=48000:cl=stereo"]
                a_parts.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,"
                               f"apad,atrim=0:{sc.duration:.3f},asetpts=PTS-STARTPTS[a{i}]")
            a_fc = ";".join(a_parts) + ";" + "".join(f"[a{i}]" for i in range(n)) + \
                f"concat=n={n}:v=0:a=1[voice]"
            if req.music_path:
                a_inputs += ["-stream_loop", "-1", "-i", str(safe_path(req.music_path))]
                a_fc += (f";[{n}:a]aresample=48000,aformat=channel_layouts=stereo,"
                         f"atrim=0:{total:.3f},volume={req.music_volume}[mus];"
                         f"[voice][mus]amix=inputs=2:duration=first:normalize=0[mix];"
                         f"[mix]loudnorm=I=-14:TP=-1.5:LRA=11[aout]")
            else:
                a_fc += ";[voice]loudnorm=I=-14:TP=-1.5:LRA=11[aout]"
            audio = tmp / "audio.m4a"
            run(["ffmpeg", "-y", *a_inputs, "-filter_complex", a_fc, "-map", "[aout]",
                 "-ar", "48000", "-c:a", "aac", "-b:a", "192k", str(audio)])
            job["progress"] = 0.85

            # 4) mux + altyazı gömme
            vf = []
            if req.subtitle_path:
                srt = safe_path(req.subtitle_path)
                local_srt = tmp / "subs.srt"
                shutil.copy(srt, local_srt)
                vf = ["-vf", f"subtitles={local_srt}:force_style='{_srt_style()}'"]
            run(["ffmpeg", "-y", "-i", str(visual), "-i", str(audio), *vf,
                 "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "medium",
                 "-crf", "19", "-pix_fmt", "yuv420p", "-profile:v", "high",
                 "-c:a", "copy", "-t", f"{total:.3f}", "-movflags", "+faststart", str(out_path)])

            info = probe(out_path)
            job.update(status="done", progress=1.0, output_path=str(out_path.relative_to(MEDIA_ROOT)),
                       duration=float(info["format"]["duration"]), finished_at=time.time())
        except Exception as e:  # noqa: BLE001 — iş durumu olarak raporlanır
            job.update(status="failed", error=str(e)[-2000:], finished_at=time.time())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ kalite probu
def _fingerprint(path: Path, ts: float) -> tuple[str, tuple[float, float, float]] | None:
    """Karenin üst %55'inin (altyazı bölgesi hariç) 256 bit fark hash'i (dHash 17x16) + ortalama RGB rengi.
    Tekrar eden sahne tespiti için: ikisi de yakınsa sahneler görsel olarak aynıdır."""
    proc = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{ts:.3f}", "-i", str(path), "-frames:v", "1",
                           "-vf", "crop=iw:ih*0.55:0:0,scale=17:16,format=rgb24", "-f", "rawvideo", "-"],
                          capture_output=True, timeout=60)
    px = proc.stdout
    if len(px) != 17 * 16 * 3:
        return None
    gray = [0.299 * px[i] + 0.587 * px[i + 1] + 0.114 * px[i + 2] for i in range(0, len(px), 3)]
    bits = "".join("1" if gray[r * 17 + c] > gray[r * 17 + c + 1] else "0" for r in range(16) for c in range(16))
    n = len(px) // 3
    mean = tuple(sum(px[i::3]) / n for i in range(3))
    return bits, mean


def _detect(path: Path, filt: str, key: str) -> list[str]:
    proc = subprocess.run(["ffmpeg", "-v", "info", "-i", str(path), "-vf", filt, "-an",
                           "-f", "null", "-"], capture_output=True, text=True, timeout=300)
    return [ln.strip() for ln in proc.stderr.splitlines() if key in ln]


def _mean_volume(path: Path) -> float | None:
    proc = subprocess.run(["ffmpeg", "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"],
                          capture_output=True, text=True, timeout=300)
    for ln in proc.stderr.splitlines():
        if "mean_volume:" in ln:
            return float(ln.split("mean_volume:")[1].split("dB")[0])
    return None


@app.get("/health")
def health():
    ff = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.split("\n")[0]
    return {"status": "ok", "ffmpeg": ff, "media_root": str(MEDIA_ROOT),
            "active_jobs": sum(1 for j in _jobs.values() if j["status"] in ("queued", "processing"))}


@app.post("/render", dependencies=[Depends(auth)])
def submit_render(req: RenderRequest):
    # aynı video için çalışan iş varsa yenisini başlatma (tekrar üretim koruması)
    for jid, j in _jobs.items():
        if j["video_id"] == req.video_id and j["status"] in ("queued", "processing"):
            return {"job_id": jid, "status": j["status"], "deduplicated": True}
    for sc in req.scenes:
        for p in (sc.image_path, sc.clip_path, sc.voice_path):
            if p:
                safe_path(p)
    job_id = uuid.uuid4().hex
    _jobs[job_id] = {"job_id": job_id, "video_id": req.video_id, "status": "queued",
                     "progress": 0.0, "created_at": time.time()}
    threading.Thread(target=render, args=(job_id, req), daemon=True).start()
    return {"job_id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}", dependencies=[Depends(auth)])
def job_status(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, "job not found")
    return _jobs[job_id]


@app.post("/probe", dependencies=[Depends(auth)])
def probe_video(req: ProbeRequest):
    path = safe_path(req.path)
    try:
        info = probe(path)
    except Exception as e:  # noqa: BLE001
        return {"opens": False, "error": str(e)}
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    prints = [_fingerprint(path, ts) for ts in req.scene_midpoints]
    dup_pairs = []
    for i in range(len(prints)):
        for j in range(i + 1, len(prints)):
            if prints[i] and prints[j]:
                dist = sum(c1 != c2 for c1, c2 in zip(prints[i][0], prints[j][0]))
                color = max(abs(a - b) for a, b in zip(prints[i][1], prints[j][1]))
                if dist <= 8 and color < 5:
                    dup_pairs.append([i, j, dist, round(color, 1)])
    # bozuk görüntü taraması: tüm kareler ve ses baştan sona çözülür; tek bir hata bile raporlanır
    dec = subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"],
                         capture_output=True, text=True, timeout=900)
    decode_errors = [ln for ln in dec.stderr.splitlines() if ln.strip()]
    if dec.returncode != 0 and not decode_errors:
        decode_errors = [f"ffmpeg çıkış kodu {dec.returncode}"]
    # önizleme sayfası: 2 sn'de bir kare, 6 sütunlu tek görsel (panelde onaydan önce gösterilir)
    sheet = path.with_name(path.stem + "_sheet.jpg")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-vf",
                    "fps=1/2,scale=180:320,tile=6x4:padding=4:color=white", "-frames:v", "1", "-q:v", "4",
                    str(sheet)], capture_output=True, timeout=300)
    return {
        "opens": True,
        "decode_errors": decode_errors[:20],
        "contact_sheet": str(sheet.relative_to(MEDIA_ROOT)) if sheet.exists() else None,
        "format_duration": float(info["format"].get("duration", 0)),
        "size_bytes": int(info["format"].get("size", 0)),
        "video": v and {"codec": v["codec_name"], "width": v["width"], "height": v["height"],
                        "duration": float(v.get("duration", 0)), "fps": v.get("r_frame_rate")},
        "audio": a and {"codec": a["codec_name"], "channels": a.get("channels"),
                        "duration": float(a.get("duration", 0))},
        "mean_volume_db": _mean_volume(path) if a else None,
        "black_segments": _detect(path, "blackdetect=d=0.5:pix_th=0.10", "black_start"),
        "freeze_segments": _detect(path, "freezedetect=n=0.001:d=2.5", "freeze_duration"),
        "duplicate_scene_pairs": dup_pairs,
    }


class ScenesRequest(BaseModel):
    path: str
    threshold: float = Field(default=0.3, gt=0, lt=1)   # 0-1: sahne değişimi hassasiyeti


@app.post("/scenes", dependencies=[Depends(auth)])
def detect_scenes(req: ScenesRequest):
    """Uzun bir videodaki sahne geçişlerini (saniye) ve süreyi döndürür.
    Bölümler sahne ortasından değil, bu noktalardan kesilir."""
    path = safe_path(req.path)
    info = probe(path)
    duration = float(info["format"]["duration"])
    proc = subprocess.run(["ffmpeg", "-v", "info", "-i", str(path), "-an",
                           "-vf", f"scale=320:-2,select='gt(scene,{req.threshold})',showinfo",
                           "-f", "null", "-"], capture_output=True, text=True, timeout=1800)
    cuts = []
    for ln in proc.stderr.splitlines():
        if "showinfo" in ln and "pts_time:" in ln:
            try:
                cuts.append(round(float(ln.split("pts_time:")[1].split()[0]), 3))
            except ValueError:
                pass
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), {})
    return {"duration": duration, "cuts": sorted(set(cuts)), "has_audio": _has_audio(path),
            "width": v.get("width"), "height": v.get("height")}
