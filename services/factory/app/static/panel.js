// AI Shorts Factory paneli — tüm istekler oturum çerezi + X-Panel başlığıyla yapılır.
const STAGES = ["scripted","media_pending","media_ready","voiced","rendering","rendered","qc_passed","qc_failed",
  "approved","rejected","uploading","uploaded","scheduled","published","failed"];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const $ = s => document.querySelector(s);
const charts = {};

async function api(path, opts = {}) {
  const r = await fetch(path, {...opts, headers: {"Content-Type": "application/json", "X-Panel": "1", ...(opts.headers||{})}});
  if (r.status === 401) { location.href = "/login"; return; }
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.message || body.detail || r.statusText);
  return body;
}
const fmtDate = d => d ? new Date(d).toLocaleString("tr-TR") : "";

document.querySelectorAll("#tabs button").forEach(b => b.onclick = () => {
  document.querySelectorAll("#tabs button, .tab").forEach(x => x.classList.remove("active"));
  b.classList.add("active"); $("#" + b.dataset.tab).classList.add("active"); load(b.dataset.tab);
});

async function loadOverview() {
  const s = await api("/api/status");
  const st = s.settings;
  $("#auto-state").innerHTML = st.automation_enabled ? '<b class="ok">AÇIK</b>' : '<b class="bad">KAPALI</b>';
  $("#auto-toggle").textContent = st.automation_enabled ? "Otomasyonu durdur" : "Otomasyonu başlat";
  $("#auto-toggle").onclick = async () => { await api("/api/settings", {method: "PUT", body: JSON.stringify({values: {automation_enabled: !st.automation_enabled}})}); loadOverview(); };
  $("#today").innerHTML = `Maliyet: <b>${s.cost_today_usd.toFixed(3)} $</b> / ${st.daily_cost_limit_usd} $<br>Video hedefi: ${st.daily_video_target}<br>Worker: <span class="${s.worker.status === "ok" ? "ok" : "bad"}">${esc(s.worker.status)}</span>`;
  $("#secrets").innerHTML = Object.entries(s.secrets_present).map(([k, v]) => `<div>${v ? "✅" : "⬜"} ${esc(k)}</div>`).join("") + '<p class="muted">Anahtar değerleri gösterilmez.</p>';
  $("#stages").innerHTML = Object.entries(s.video_stages).map(([k, v]) => `<span class="badge">${esc(k)}: ${v}</span> `).join("") || '<span class="muted">Henüz video yok</span>';
  const f = $("#settings");
  for (const k of ["daily_video_target","language","min_duration_sec","max_duration_sec","daily_cost_limit_usd","max_retries","content_mode","upload_privacy"]) f[k].value = st[k];
  f.categories.value = (st.categories || []).join(", ");
  f.public_publishing_enabled.checked = !!st.public_publishing_enabled;
  f.require_manual_approval.checked = !!st.require_manual_approval;
  for (const kind of ["llm","image","video","tts"]) {
    const sel = f["p_" + kind];
    sel.innerHTML = ['<option value="none">— yok —</option>', ...s.providers_available[kind].map(p => `<option>${esc(p)}</option>`)].join("");
    sel.value = st.providers[kind] || "none";
  }
}
$("#settings").onsubmit = async e => {
  e.preventDefault(); const f = e.target;
  if (f.public_publishing_enabled.checked && !confirm("Herkese açık yayına izin vermek istediğinize emin misiniz? Videolar onaydan sonra herkese açık yüklenebilir.")) return;
  const values = {daily_video_target: +f.daily_video_target.value, language: f.language.value,
    categories: f.categories.value.split(",").map(x => x.trim()).filter(Boolean),
    min_duration_sec: +f.min_duration_sec.value, max_duration_sec: +f.max_duration_sec.value,
    daily_cost_limit_usd: +f.daily_cost_limit_usd.value, max_retries: +f.max_retries.value,
    content_mode: f.content_mode.value, upload_privacy: f.upload_privacy.value,
    public_publishing_enabled: f.public_publishing_enabled.checked, require_manual_approval: f.require_manual_approval.checked,
    providers: {llm: f.p_llm.value, image: f.p_image.value, video: f.p_video.value, tts: f.p_tts.value}};
  try { await api("/api/settings", {method: "PUT", body: JSON.stringify({values})}); $("#settings-msg").textContent = "Kaydedildi ✓"; }
  catch (err) { $("#settings-msg").textContent = "Hata: " + err.message; }
};

async function loadVideos() {
  const sel = $("#stage-filter");
  if (sel.options.length === 1) { STAGES.forEach(s => sel.add(new Option(s, s))); sel.onchange = loadVideos; }
  const rows = await api("/api/videos" + (sel.value ? "?stage=" + sel.value : ""));
  $("#video-table tbody").innerHTML = rows.map(v => `<tr>
    <td>${v.series_title ? `<span class="badge">${esc(v.series_title)} · B${v.episode_number}</span> ` : ""}${esc(v.title)}<div class="muted">${esc(v.last_error || "")}</div></td>
    <td><span class="badge">${esc(v.stage)}</span></td><td>${v.duration_sec ?? ""}</td><td>${(+v.cost_usd).toFixed(3)}</td>
    <td>${v.qc_passed === null ? "" : v.qc_passed ? '<span class="ok">✓</span>' : '<span class="bad">✗</span>'}</td>
    <td>${v.youtube_url ? `<a href="${esc(v.youtube_url)}" target="_blank" rel="noopener">${esc(v.publish_status)}</a>` : ""}</td>
    <td><button class="secondary" data-id="${v.id}">Ayrıntı</button></td></tr>`).join("");
  document.querySelectorAll("#video-table button[data-id]").forEach(b => b.onclick = () => showVideo(b.dataset.id));
}

async function showVideo(id) {
  const v = await api("/api/videos/" + id); const box = $("#video-detail"); box.hidden = false;
  const qc = v.qc_result;
  const qcHtml = qc ? Object.entries(qc.checks).map(([k, c]) => `<div class="${c.ok ? "ok" : "bad"}">${c.ok ? "✓" : "✗"} ${esc(k)} <span class="muted">${esc(c.detail)}</span></div>`).join("")
    + (qc.warnings || []).map(w => `<div class="warn">⚠ ${esc(w)}</div>`).join("") : '<span class="muted">QC çalışmadı</span>';
  box.innerHTML = `<h2>${esc(v.title)}</h2>
    <div class="grid"><div>${v.file_path ? `<video controls src="/media/${esc(v.file_path)}"></video>` : '<p class="muted">Render yok</p>'}</div>
    <div><h3>Kalite kontrol</h3>${qcHtml}
      ${qc && qc.probe && qc.probe.contact_sheet ? `<h3>Tüm video (2 sn'de bir kare)</h3><a href="/media/${esc(qc.probe.contact_sheet)}" target="_blank" rel="noopener"><img src="/media/${esc(qc.probe.contact_sheet)}" alt="önizleme sayfası" style="max-width:100%;border-radius:8px;border:1px solid var(--line)"></a>` : ""}
      <p>${v.stage === "qc_passed" ? '<button id="btn-approve">Onayla (gizli yükleme kuyruğu)</button> ' : ""}
      ${["qc_passed","qc_failed","rendered"].includes(v.stage) ? '<button class="secondary" id="btn-reject">Reddet</button>' : ""}</p></div></div>
    <h3>Senaryo</h3><ol>${v.body.scenes.map(s => `<li>${esc(s.text)}<div class="muted">${esc(s.visual_prompt)}</div></li>`).join("")}</ol>
    <h3>YouTube metadata</h3><pre>${esc(JSON.stringify(v.youtube_metadata, null, 2))}</pre>
    <h3>Varlıklar ve lisanslar</h3><table><tr><th>Tür</th><th>#</th><th>Sağlayıcı</th><th>Durum</th><th>Lisans</th><th>$</th></tr>
    ${v.assets.map(a => `<tr><td>${esc(a.kind)}</td><td>${a.scene_index ?? ""}</td><td>${esc(a.provider)}</td><td>${esc(a.status)} ${esc(a.error || "")}</td><td>${esc(a.license_kind || "—")} ${a.verified ? "✓" : ""}</td><td>${(+a.cost_usd).toFixed(3)}</td></tr>`).join("")}</table>`;
  const ap = $("#btn-approve"); if (ap) ap.onclick = async () => { await api(`/api/videos/${id}/approve`, {method: "POST"}); showVideo(id); loadVideos(); };
  const rj = $("#btn-reject"); if (rj) rj.onclick = async () => { const r = prompt("Ret nedeni?") ?? ""; await api(`/api/videos/${id}/reject?reason=${encodeURIComponent(r)}`, {method: "POST"}); showVideo(id); loadVideos(); };
  box.scrollIntoView({behavior: "smooth"});
}

async function loadIdeas() {
  const rows = await api("/api/ideas");
  $("#idea-table tbody").innerHTML = rows.map(i => `<tr><td>${(+i.score).toFixed(0)}</td><td>${esc(i.topic)}<div class="muted">${esc(i.angle || "")}</div></td><td>${esc(i.category || "")}</td><td>${esc(i.source || "")}</td>
    <td><button class="secondary" data-id="${i.id}">Senaryo üret</button></td></tr>`).join("");
  document.querySelectorAll("#idea-table button").forEach(b => b.onclick = async () => {
    try { const r = await api("/api/scripts/generate", {method: "POST", body: JSON.stringify({idea_id: b.dataset.id})}); alert("Video oluşturuldu: " + r.video_id); loadIdeas(); }
    catch (e) { alert("Senaryo üretilemedi: " + e.message); }
  });
}
$("#idea-form").onsubmit = async e => {
  e.preventDefault(); const f = e.target;
  const r = await api("/api/ideas", {method: "POST", body: JSON.stringify({topic: f.topic.value, category: f.category.value || null, angle: f.angle.value || null})});
  $("#idea-msg").textContent = r.created ? "Eklendi ✓" : "Eklenmedi: " + r.reason; if (r.created) f.reset(); loadIdeas();
};

async function loadCalendar() {
  const rows = (await api("/api/videos")).filter(v => v.youtube_url || v.scheduled_at || v.stage === "approved");
  rows.sort((a, b) => new Date(b.scheduled_at || b.published_at || b.created_at) - new Date(a.scheduled_at || a.published_at || a.created_at));
  $("#cal-table tbody").innerHTML = rows.map(v => `<tr><td>${fmtDate(v.scheduled_at || v.published_at)}</td><td>${esc(v.title)}</td><td>${esc(v.stage)} / ${esc(v.publish_status)}</td>
    <td>${v.youtube_url ? `<a href="${esc(v.youtube_url)}" target="_blank" rel="noopener">aç</a>` : ""}</td></tr>`).join("") || '<tr><td colspan="4" class="muted">Henüz yayın yok</td></tr>';
}

function chart(id, type, labels, datasets) {
  if (!window.Chart) return;
  charts[id]?.destroy();
  charts[id] = new Chart(document.getElementById(id), {type, data: {labels, datasets}, options: {responsive: true, plugins: {legend: {position: "bottom"}}}});
}

async function loadCosts() {
  const c = await api("/api/costs");
  $("#cost-table tbody").innerHTML = c.by_day.map(r => `<tr><td>${esc(r.day)}</td><td>${esc(r.provider)}</td><td>${r.calls}</td><td>${r.failures}</td><td>${(+r.cost).toFixed(4)}</td></tr>`).join("");
  const days = [...new Set(c.by_day.map(r => r.day))].reverse(), provs = [...new Set(c.by_day.map(r => r.provider))];
  chart("cost-chart", "bar", days, provs.map(p => ({label: p, data: days.map(d => +(c.by_day.find(r => r.day === d && r.provider === p)?.cost || 0)), stack: "s"})));
}

async function loadPerf() {
  const rows = await api("/api/analytics/performance");
  chart("perf-chart", "bar", rows.map(r => (r.title || "").slice(0, 24)), [{label: "Görüntülenme", data: rows.map(r => r.views || 0)},
    {label: "Ort. izlenme %", data: rows.map(r => +r.avg_view_pct || 0)}]);
}
$("#run-opt").onclick = async () => { $("#opt-out").textContent = "Çalışıyor…"; try { $("#opt-out").textContent = JSON.stringify(await api("/api/optimizer/run", {method: "POST"}), null, 2); } catch (e) { $("#opt-out").textContent = e.message; } };

async function loadErrors() {
  const rows = await api("/api/errors");
  $("#err-table tbody").innerHTML = rows.map(e => `<tr class="${e.resolved ? "muted" : ""}"><td>${fmtDate(e.created_at)}</td><td>${esc(e.source)}</td><td>${esc(e.message)}</td>
    <td>${e.resolved ? "çözüldü" : `<button class="secondary" data-id="${e.id}">Çözüldü</button>`}</td></tr>`).join("");
  document.querySelectorAll("#err-table button").forEach(b => b.onclick = async () => { await api(`/api/errors/${b.dataset.id}/resolve`, {method: "POST"}); loadErrors(); });
}

async function loadSeries() {
  const [ser, src] = await Promise.all([api("/api/series"), api("/api/sources")]);
  const fmtDur = s => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
  $("#series-table tbody").innerHTML = ser.map(s => `<tr>
    <td><b>${esc(s.title)}</b>${s.made_for_kids ? ' <span class="badge">çocuk</span>' : ""}<div class="muted">${esc(s.source_title || s.brief || "")}</div></td>
    <td>${s.kind === "pd_clips" ? "Film kesitleri" : "Özgün"}</td><td><span class="badge">${esc(s.status)}</span>${s.narration ? "" : ' <span class="badge">sessiz</span>'}</td>
    <td>${s.produced}${s.total_episodes ? " / " + s.total_episodes : ""}</td><td>${s.uploaded}</td>
    <td>${s.playlist_id ? `<a href="https://www.youtube.com/playlist?list=${esc(s.playlist_id)}" target="_blank" rel="noopener">aç</a>` : ""}</td>
    <td><button class="secondary" data-next="${s.id}">Sonraki bölüm</button>
        <button class="secondary" data-status="${s.id}" data-to="${s.status === "active" ? "paused" : "active"}">${s.status === "active" ? "Duraklat" : "Devam et"}</button></td></tr>`).join("")
    || '<tr><td colspan="7" class="muted">Henüz seri yok</td></tr>';
  document.querySelectorAll("[data-next]").forEach(b => b.onclick = async () => {
    b.disabled = true;
    try { const r = await api(`/api/series/${b.dataset.next}/next`, {method: "POST"}); alert(`Bölüm ${r.episode} oluşturuldu. Üretim zinciri (görsel → ses → montaj → QC) otomatik devam eder; otomasyon kapalıysa Videolar sekmesinden takip edin.`); }
    catch (e) { alert("Bölüm oluşturulamadı: " + e.message); }
    loadSeries();
  });
  document.querySelectorAll("[data-status]").forEach(b => b.onclick = async () => {
    await api(`/api/series/${b.dataset.status}/status`, {method: "POST", body: JSON.stringify({status: b.dataset.to})}); loadSeries();
  });
  $("#series-form").source_id.innerHTML = '<option value="">—</option>' + src.map(x => `<option value="${x.id}">${esc(x.title)} (${x.episodes} bölüm${x.verified ? "" : ", LİSANS DOĞRULANMADI"})</option>`).join("");
  $("#source-table tbody").innerHTML = src.map(x => `<tr><td>${esc(x.title)}<div class="muted">${esc(x.file_path)}</div></td>
    <td>${fmtDur(+x.duration_sec)}</td><td>${x.episodes}</td><td>${x.subtitle_path ? '<span class="ok">TR ✓</span>' : '<span class="muted">yok</span>'}</td>
    <td>${esc(x.license_name)} ${x.verified ? '<span class="ok">✓ doğrulandı</span>' : '<span class="bad">✗ doğrulanmadı</span>'}
      ${x.proof_url ? `<a href="${esc(x.proof_url)}" target="_blank" rel="noopener">kanıt</a>` : ""}</td></tr>`).join("")
    || '<tr><td colspan="5" class="muted">Henüz kaynak yok</td></tr>';
}
$("#series-form").onsubmit = async e => {
  e.preventDefault(); const f = e.target;
  const body = {title: f.title.value, kind: f.kind.value, description: f.description.value, source_id: f.source_id.value || null,
    brief: f.brief.value || null, made_for_kids: f.made_for_kids.checked, buffer_size: +f.buffer_size.value,
    narration: f.narration.checked, keep_source_audio: f.keep_source_audio.checked, music_file: f.music_file.value.trim() || null,
    music_license_url: f.music_license_url.value || null, music_confirmed: f.music_confirmed.checked};
  try { await api("/api/series", {method: "POST", body: JSON.stringify(body)}); $("#series-msg").textContent = "Seri oluşturuldu ✓"; f.reset(); loadSeries(); }
  catch (err) { $("#series-msg").textContent = "Hata: " + err.message; }
};
$("#source-form").onsubmit = async e => {
  e.preventDefault(); const f = e.target;
  $("#source-msg").textContent = "Film inceleniyor, sahne geçişleri bulunuyor (uzun filmlerde birkaç dakika sürebilir)…";
  const body = {file_name: f.file_name.value.trim(), title: f.title.value, license_kind: f.license_kind.value,
    license_name: f.license_name.value || null, source_url: f.source_url.value || null, proof_url: f.proof_url.value || null,
    confirmed: f.confirmed.checked, subtitle_file: f.subtitle_file.value.trim() || null};
  try { const r = await api("/api/sources", {method: "POST", body: JSON.stringify(body)});
    $("#source-msg").textContent = `Kaydedildi ✓ — ${r.episodes} bölüme ayrıldı.` + (r.warning ? " ⚠ " + r.warning : ""); f.reset(); loadSeries(); }
  catch (err) { $("#source-msg").textContent = "Hata: " + err.message; }
};

const loaders = {series: loadSeries, overview: loadOverview, videos: loadVideos, ideas: loadIdeas, calendar: loadCalendar, costs: loadCosts, perf: loadPerf, errors: loadErrors};
function load(tab) { loaders[tab]().catch(e => console.error(e)); }
load("overview");
