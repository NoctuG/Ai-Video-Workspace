const state = {
  project: null,
  chapter: null,
  shots: [],
  selectedShotIds: new Set(),
  assets: [],
  timeline: [],
  tasks: new Map(),
};

const els = {
  projectName: document.getElementById("projectName"),
  stylePrompt: document.getElementById("stylePrompt"),
  baseSeed: document.getElementById("baseSeed"),
  createProjectBtn: document.getElementById("createProjectBtn"),
  dashboardBox: document.getElementById("dashboardBox"),
  chapterTitle: document.getElementById("chapterTitle"),
  chapterScript: document.getElementById("chapterScript"),
  createChapterBtn: document.getElementById("createChapterBtn"),
  shotsTable: document.getElementById("shotsTable"),
  batchDuration: document.getElementById("batchDuration"),
  batchEmotion: document.getElementById("batchEmotion"),
  applyBatchBtn: document.getElementById("applyBatchBtn"),
  generateSelectedBtn: document.getElementById("generateSelectedBtn"),
  reloadAssetsBtn: document.getElementById("reloadAssetsBtn"),
  assetsBox: document.getElementById("assetsBox"),
  timelineBox: document.getElementById("timelineBox"),
  saveTimelineBtn: document.getElementById("saveTimelineBtn"),
  exportBtn: document.getElementById("exportBtn"),
  preview: document.getElementById("preview"),
};

function api(path) {
  const base = window.__AI_VIDEO_WORKSPACE__?.API_BASE || "http://127.0.0.1:8000";
  return `${base.replace(/\/$/, "")}${path}`;
}

async function request(path, method = "GET", body) {
  const res = await fetch(api(path), {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    throw new Error(await res.text());
  }
  return res.json();
}

function renderPreview(lastAction) {
  els.preview.textContent = JSON.stringify(
    {
      lastAction,
      project: state.project,
      chapter: state.chapter,
      shotCount: state.shots.length,
      selectedShots: [...state.selectedShotIds],
      assets: state.assets,
      timeline: state.timeline,
      tasks: [...state.tasks.values()].map((x) => ({ id: x.id, status: x.status, progress: x.progress })),
    },
    null,
    2
  );
}

function renderDashboard(data) {
  if (!data) {
    els.dashboardBox.textContent = "暂无项目";
    return;
  }
  els.dashboardBox.innerHTML = `
    <div>项目：<strong>${data.project.name}</strong></div>
    <div>章节数：${data.chapter_count}</div>
    <div>分镜数：${data.shot_count}</div>
    <div>已生成素材：${data.generated_count}</div>
    <div>全局风格：${data.project.style_prompt}</div>
    <div>全局种子：${data.project.base_seed}</div>
  `;
}

function renderShots() {
  if (!state.shots.length) {
    els.shotsTable.innerHTML = "<p>暂无分镜</p>";
    return;
  }
  const rows = state.shots
    .map((s) => {
      const checked = state.selectedShotIds.has(s.id) ? "checked" : "";
      return `<tr>
      <td><input type='checkbox' data-shot-check='${s.id}' ${checked}/></td>
      <td>${s.order}</td>
      <td>${s.content}</td>
      <td>${s.camera_size}</td>
      <td>${s.emotion}</td>
      <td>${s.duration_sec}s</td>
      <td><button class='btn mini' data-generate='${s.id}'>生成视频</button></td>
    </tr>`;
    })
    .join("");

  els.shotsTable.innerHTML = `
    <table>
      <thead><tr><th>选中</th><th>#</th><th>内容</th><th>景别</th><th>情绪</th><th>时长</th><th>操作</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderAssets() {
  if (!state.assets.length) {
    els.assetsBox.innerHTML = "<p>暂无生成素材</p>";
    return;
  }
  els.assetsBox.innerHTML = state.assets
    .map(
      (a) => `<div class='asset-item'>
      <div><strong>${a.type}</strong> · ${a.quality}</div>
      <div>shot: ${a.shot_id.slice(0, 8)}...</div>
      <a href='${a.url}' target='_blank'>${a.url}</a>
      <button class='btn mini' data-add-clip='${a.id}'>加入时间线</button>
    </div>`
    )
    .join("");
}

function renderTimeline() {
  if (!state.timeline.length) {
    els.timelineBox.innerHTML = "<p>时间线为空</p>";
    return;
  }
  els.timelineBox.innerHTML = state.timeline
    .map(
      (c, idx) => `<div class='timeline-item'>
      <span>#${idx + 1} ${c.asset_id.slice(0, 8)}... (${c.duration_sec}s)</span>
      <button class='btn mini danger' data-remove-clip='${idx}'>移除</button>
    </div>`
    )
    .join("");
}

async function refreshDashboard() {
  if (!state.project) return;
  const dash = await request(`/api/projects/${state.project.id}/dashboard`);
  renderDashboard(dash);
}

async function createProject() {
  state.project = await request("/api/projects", "POST", {
    name: els.projectName.value,
    style_prompt: els.stylePrompt.value,
    base_seed: Number(els.baseSeed.value),
    style_lock: true,
  });
  await refreshDashboard();
  renderPreview("create-project");
}

async function createChapter() {
  if (!state.project) throw new Error("请先创建项目");
  const data = await request(`/api/projects/${state.project.id}/chapters`, "POST", {
    title: els.chapterTitle.value,
    script: els.chapterScript.value,
  });
  state.chapter = data.chapter;
  state.shots = data.shots;
  state.selectedShotIds.clear();
  renderShots();
  await refreshDashboard();
  renderPreview("create-chapter");
}

async function applyBatch() {
  const shot_ids = [...state.selectedShotIds];
  if (!shot_ids.length) return;
  await request("/api/shots/batch-update", "POST", {
    shot_ids,
    patch: {
      duration_sec: Number(els.batchDuration.value),
      emotion: els.batchEmotion.value,
    },
  });
  state.shots = await Promise.all(state.shots.map((s) => request(`/api/shots/${s.id}`, "PATCH", {})));
  renderShots();
  renderPreview("batch-update");
}

async function generateShot(shotId) {
  if (!state.project) return;
  const data = await request(`/api/shots/${shotId}/generate`, "POST", {
    model: "seedance-1.5",
    duration_sec: 5,
  });
  state.tasks.set(data.task.id, data.task);
  await loadAssets();
  await refreshDashboard();
  renderPreview(`generate-shot:${shotId}`);
}

async function generateSelected() {
  for (const shotId of state.selectedShotIds) {
    await generateShot(shotId);
  }
}

async function loadAssets() {
  if (!state.project) return;
  state.assets = await request(`/api/projects/${state.project.id}/generated-assets`);
  renderAssets();
}

async function saveTimeline() {
  if (!state.project) return;
  const timeline = await request(`/api/projects/${state.project.id}/timeline`, "POST", {
    clips: state.timeline,
    bgm_url: null,
  });
  renderPreview(`save-timeline:${timeline.clips.length}`);
}

async function exportProject() {
  if (!state.project) return;
  const result = await request(`/api/projects/${state.project.id}/export`, "POST", {});
  renderPreview(`export:${result.export_url}`);
}

function connectWs() {
  const url = api("/ws/tasks").replace(/^http/, "ws");
  const ws = new WebSocket(url);
  ws.onopen = () => ws.send("subscribe");
  ws.onmessage = (evt) => {
    const payload = JSON.parse(evt.data);
    if (payload.task) {
      state.tasks.set(payload.task.id, payload.task);
      renderPreview(`ws:${payload.event}`);
    }
  };
  ws.onclose = () => setTimeout(connectWs, 1500);
}

function bindEvents() {
  els.createProjectBtn.addEventListener("click", () => createProject().catch((e) => renderPreview(e.message)));
  els.createChapterBtn.addEventListener("click", () => createChapter().catch((e) => renderPreview(e.message)));
  els.applyBatchBtn.addEventListener("click", () => applyBatch().catch((e) => renderPreview(e.message)));
  els.generateSelectedBtn.addEventListener("click", () => generateSelected().catch((e) => renderPreview(e.message)));
  els.reloadAssetsBtn.addEventListener("click", () => loadAssets().catch((e) => renderPreview(e.message)));
  els.saveTimelineBtn.addEventListener("click", () => saveTimeline().catch((e) => renderPreview(e.message)));
  els.exportBtn.addEventListener("click", () => exportProject().catch((e) => renderPreview(e.message)));

  document.body.addEventListener("click", (evt) => {
    const target = evt.target;
    if (!(target instanceof HTMLElement)) return;

    const shotCheck = target.getAttribute("data-shot-check");
    if (shotCheck) {
      if (target.checked) state.selectedShotIds.add(shotCheck);
      else state.selectedShotIds.delete(shotCheck);
      return;
    }

    const generateId = target.getAttribute("data-generate");
    if (generateId) {
      generateShot(generateId).catch((e) => renderPreview(e.message));
      return;
    }

    const addClip = target.getAttribute("data-add-clip");
    if (addClip) {
      const asset = state.assets.find((x) => x.id === addClip);
      if (!asset) return;
      state.timeline.push({ asset_id: asset.id, duration_sec: 5, url: asset.url });
      renderTimeline();
      renderPreview("add-clip");
      return;
    }

    const removeClipIndex = target.getAttribute("data-remove-clip");
    if (removeClipIndex !== null) {
      state.timeline.splice(Number(removeClipIndex), 1);
      renderTimeline();
      renderPreview("remove-clip");
    }
  });
}

function bootstrap() {
  els.chapterScript.value = "场景1：夜雨街道\n主角: 我必须在黎明前找到她。\n配角: 我只给你三分钟。\n场景2：天台对峙\n主角: 真相就在这份文件里。";
  renderDashboard(null);
  renderShots();
  renderAssets();
  renderTimeline();
  renderPreview("bootstrap");
  bindEvents();
  connectWs();
}

bootstrap();
