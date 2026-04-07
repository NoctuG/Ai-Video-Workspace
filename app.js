const WORKFLOW = [
  "1) 系统配置(API/图床/路由)",
  "2) 剧本导入与结构化拆解",
  "3) AI 二次深化校准",
  "4) 可选素材库生成",
  "5) 导演分镜批量生成",
  "6) S级多模态成片",
];

const state = {
  projectId: null,
  project: null,
  calibration: null,
  settings: null,
  tasks: new Map(),
};

const els = {
  workflowList: document.getElementById("workflowList"),
  taskBoard: document.getElementById("taskBoard"),
  preview: document.getElementById("preview"),
  apiBase: document.getElementById("apiBase"),
  settingsJson: document.getElementById("settingsJson"),
  projectName: document.getElementById("projectName"),
  scriptInput: document.getElementById("scriptInput"),
  calibStyle: document.getElementById("calibStyle"),
  calibModel: document.getElementById("calibModel"),
  storyboardIds: document.getElementById("storyboardIds"),
  sclassJson: document.getElementById("sclassJson"),
};

function api(path) {
  return `${els.apiBase.value.replace(/\/$/, "")}${path}`;
}

async function post(path, body) {
  const r = await fetch(api(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

async function get(path) {
  const r = await fetch(api(path));
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

function ids() {
  return els.storyboardIds.value
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
}

function renderWorkflow() {
  els.workflowList.innerHTML = WORKFLOW.map((x) => `<li>${x}</li>`).join("");
}

function renderTasks() {
  const arr = [...state.tasks.values()].sort((a, b) => b.updated_at - a.updated_at);
  if (!arr.length) {
    els.taskBoard.textContent = "暂无任务";
    return;
  }
  els.taskBoard.innerHTML = arr
    .map(
      (t) => `<div class='task-item'>
      <strong>${t.task_type}</strong> · ${t.status} (${t.progress}%)<br/>
      <small>${t.id.slice(0, 8)}... attempt ${t.attempt}/${t.max_retries + 1}</small><br/>
      <small>${t.message}</small>
    </div>`
    )
    .join("");
}

function refreshPreview(lastAction) {
  els.preview.textContent = JSON.stringify(
    {
      lastAction,
      projectId: state.projectId,
      settings: state.settings,
      project: state.project,
      calibration: state.calibration,
      tasks: [...state.tasks.values()].map((t) => ({
        id: t.id,
        type: t.task_type,
        status: t.status,
        progress: t.progress,
        result: t.result?.asset_url,
      })),
    },
    null,
    2
  );
}

function getDefaultSettings() {
  return {
    api_providers: [
      {
        name: "memefast",
        base_url: "https://api.memefast.example",
        keys: ["mf_key_1", "mf_key_2", "mf_key_3"],
      },
      {
        name: "runninghub",
        base_url: "https://api.runninghub.example",
        keys: ["rh_key_1", "rh_key_2"],
      },
    ],
    routes: [
      {
        capability: "text_to_image",
        provider_name: "memefast",
        model: "gemini-3-pro-image-preview",
      },
      {
        capability: "image_to_video",
        provider_name: "runninghub",
        model: "doubao-seedance-1-5-pro-251215",
      },
      {
        capability: "text_to_video",
        provider_name: "runninghub",
        model: "doubao-seedance-1-5-pro-251215",
      },
      {
        capability: "llm",
        provider_name: "memefast",
        model: "gpt-4.1",
      },
    ],
    image_bed: {
      provider: "oss-proxy",
      endpoint: "https://imgbed.example/upload",
      keys: ["img_key_1", "img_key_2"],
    },
  };
}

function getDefaultSClass() {
  return {
    groups: [
      { group_name: "序章", storyboard_ids: ["sb_1", "sb_2"] },
      { group_name: "冲突", storyboard_ids: ["sb_3", "sb_4", "sb_5"] },
    ],
  };
}

async function enqueueTask(task_type, input) {
  if (!state.projectId) throw new Error("请先导入剧本并创建项目");
  const task = await post("/api/tasks", { project_id: state.projectId, task_type, input });
  state.tasks.set(task.id, task);
  renderTasks();
  refreshPreview(`enqueue:${task_type}`);
}

function connectWs() {
  const url = api("/ws/tasks").replace(/^http/, "ws");
  const ws = new WebSocket(url);
  ws.onopen = () => ws.send("subscribe");
  ws.onmessage = (evt) => {
    const payload = JSON.parse(evt.data);
    if (payload.task) {
      state.tasks.set(payload.task.id, payload.task);
      renderTasks();
      refreshPreview(`ws:${payload.event}`);
    }
  };
  ws.onclose = () => setTimeout(connectWs, 1500);
}

async function bindActions() {
  document.getElementById("saveSettingsBtn").addEventListener("click", async () => {
    try {
      const payload = JSON.parse(els.settingsJson.value);
      state.settings = await post("/api/settings", payload);
      refreshPreview("save-settings");
    } catch (e) {
      refreshPreview(`save-settings-failed: ${e.message}`);
    }
  });

  document.getElementById("importScriptBtn").addEventListener("click", async () => {
    try {
      const project = await post("/api/script/import", {
        project_name: els.projectName.value,
        raw_script: els.scriptInput.value,
      });
      state.project = project;
      state.projectId = project.id;
      refreshPreview("import-script");
    } catch (e) {
      refreshPreview(`import-script-failed: ${e.message}`);
    }
  });

  document.getElementById("loadProjectBtn").addEventListener("click", async () => {
    if (!state.projectId) return;
    state.project = await get(`/api/projects/${state.projectId}`);
    refreshPreview("load-project");
  });

  document.getElementById("runCalibrationBtn").addEventListener("click", async () => {
    try {
      state.calibration = await post("/api/calibration", {
        project_id: state.projectId,
        style: els.calibStyle.value,
        target_model: els.calibModel.value,
      });
      refreshPreview("run-calibration");
    } catch (e) {
      refreshPreview(`run-calibration-failed: ${e.message}`);
    }
  });

  document.getElementById("genSceneAssetBtn").addEventListener("click", async () => {
    try {
      await enqueueTask("scene_asset_generation", { scene_count: state.project?.parsed?.scenes?.length || 0 });
    } catch (e) {
      refreshPreview(`scene-asset-failed: ${e.message}`);
    }
  });

  document.getElementById("genCharAssetBtn").addEventListener("click", async () => {
    try {
      await enqueueTask("character_asset_generation", {
        character_count: state.project?.parsed?.characters?.length || 0,
      });
    } catch (e) {
      refreshPreview(`char-asset-failed: ${e.message}`);
    }
  });

  document.getElementById("syncDirectorBtn").addEventListener("click", async () => {
    try {
      const data = await post(`/api/director/sync?project_id=${state.projectId}`, {});
      const loadedIds = data.storyboards.map((x) => x.id).join(",");
      els.storyboardIds.value = loadedIds;
      refreshPreview("director-sync");
    } catch (e) {
      refreshPreview(`director-sync-failed: ${e.message}`);
    }
  });

  document.getElementById("batchImageBtn").addEventListener("click", async () => {
    try {
      const data = await post("/api/director/batch-image", { project_id: state.projectId, storyboard_ids: ids() });
      refreshPreview(`batch-image:${data.queued}`);
    } catch (e) {
      refreshPreview(`batch-image-failed: ${e.message}`);
    }
  });

  document.getElementById("batchVideoBtn").addEventListener("click", async () => {
    try {
      const data = await post("/api/director/batch-video", { project_id: state.projectId, storyboard_ids: ids() });
      refreshPreview(`batch-video:${data.queued}`);
    } catch (e) {
      refreshPreview(`batch-video-failed: ${e.message}`);
    }
  });

  document.getElementById("runSclassBtn").addEventListener("click", async () => {
    try {
      const payload = JSON.parse(els.sclassJson.value);
      const data = await post("/api/sclass/compose", {
        project_id: state.projectId,
        groups: payload.groups,
      });
      refreshPreview(`sclass:${data.accepted_groups}`);
    } catch (e) {
      refreshPreview(`sclass-failed: ${e.message}`);
    }
  });
}

function bootstrap() {
  renderWorkflow();
  els.settingsJson.value = JSON.stringify(getDefaultSettings(), null, 2);
  els.sclassJson.value = JSON.stringify(getDefaultSClass(), null, 2);
  els.scriptInput.value = `场景1: 夜雨街道\n主角: 我必须在黎明前找到她。\n配角: 我只给你三分钟。\n场景2: 天台对峙\n主角: 真相就在这份文件里。`;
  refreshPreview("bootstrap");
  renderTasks();
  connectWs();
  bindActions();
}

bootstrap();
