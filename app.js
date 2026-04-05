const WORKFLOW = ["准备工作", "剧本", "AI校准", "场景/角色（可选）", "导演/S级", "生成视频"];

const form = document.getElementById("studioForm");
const workflowList = document.getElementById("workflowList");
const requestPreview = document.getElementById("requestPreview");
const taskBoard = document.getElementById("taskBoard");
const resultBox = document.getElementById("resultBox");
const apiBaseInput = document.getElementById("apiBase");

let projectId = null;
const tasks = new Map();

function apiBase() {
  return apiBaseInput.value.replace(/\/$/, "");
}

function formDataObj() {
  return Object.fromEntries(new FormData(form).entries());
}

function initWorkflow() {
  workflowList.innerHTML = WORKFLOW.map((s) => `<li>${s}</li>`).join("");
}

function markProgress() {
  const d = formDataObj();
  const state = {
    "准备工作": !!d.projectName,
    "剧本": !!d.script,
    "AI校准": !!(d.styleKeywords || d.negativePrompt),
    "场景/角色（可选）": !!(d.scenes || d.characterBible),
    "导演/S级": !!d.cameraDirection,
    "生成视频": tasks.size > 0,
  };
  [...workflowList.querySelectorAll("li")].forEach((li) => li.classList.toggle("done", state[li.textContent]));
}

function refreshPreview() {
  requestPreview.textContent = JSON.stringify(
    {
      projectId,
      workflow: WORKFLOW,
      payload: formDataObj(),
      queueSummary: [...tasks.values()].map((t) => ({ id: t.id, type: t.task_type, status: t.status, progress: t.progress })),
      notes: {
        architecture: "frontend + backend async queue + cloud object storage",
        aiGateway: "modelRoute can be routed by backend adapter layer",
      },
    },
    null,
    2
  );
}

function renderTasks() {
  if (tasks.size === 0) {
    taskBoard.textContent = "暂无任务";
    return;
  }
  taskBoard.innerHTML = [...tasks.values()]
    .sort((a, b) => b.updated_at - a.updated_at)
    .map(
      (t) => `
      <div class='task-item'>
        <div><strong>${t.task_type}</strong> · ${t.status}</div>
        <div>${t.id.slice(0, 8)}... · ${t.progress}%</div>
        <div>${t.message || ""}</div>
        ${t.result?.url ? `<div><a href='${t.result.url}' target='_blank'>结果 URL</a></div>` : ""}
      </div>
    `
    )
    .join("");
}

async function createProject() {
  const payload = formDataObj();
  const res = await fetch(`${apiBase()}/api/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      project_name: payload.projectName,
      workflow: WORKFLOW,
      payload,
    }),
  });
  if (!res.ok) throw new Error(await res.text());
  const data = await res.json();
  projectId = data.id;
  resultBox.textContent = `项目已创建: ${projectId}`;
  refreshPreview();
}

async function enqueue(taskType) {
  if (!projectId) {
    resultBox.textContent = "请先创建项目。";
    return;
  }

  const d = formDataObj();
  const input =
    taskType === "text_to_image"
      ? {
          prompt: [d.script, d.styleKeywords, d.characterBible, d.scenes].filter(Boolean).join("，"),
          negative_prompt: d.negativePrompt,
          route: d.modelRoute,
        }
      : {
          image_url: d.referenceImageUrl,
          director_notes: d.cameraDirection,
          duration_seconds: d.pace === "快" ? 6 : d.pace === "中" ? 8 : 12,
          route: d.modelRoute,
        };

  const res = await fetch(`${apiBase()}/api/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: projectId, task_type: taskType, input }),
  });
  if (!res.ok) throw new Error(await res.text());
  const task = await res.json();
  tasks.set(task.id, task);
  renderTasks();
  refreshPreview();
}

function startTaskSocket() {
  const url = apiBase().replace(/^http/, "ws") + "/ws/tasks";
  const ws = new WebSocket(url);
  ws.onopen = () => ws.send("subscribe");
  ws.onmessage = (evt) => {
    const payload = JSON.parse(evt.data);
    if (payload.task) {
      tasks.set(payload.task.id, payload.task);
      renderTasks();
      refreshPreview();
      if (payload.event === "task_completed") {
        resultBox.innerHTML = `任务完成：<br/>${payload.task.task_type}<br/><a href='${payload.task.result?.url}' target='_blank'>${payload.task.result?.url}</a>`;
      }
    }
    markProgress();
  };
  ws.onclose = () => setTimeout(startTaskSocket, 1500);
}

form.addEventListener("input", () => {
  markProgress();
  refreshPreview();
});

document.getElementById("scriptTemplate").addEventListener("click", () => {
  const base = "第一幕：主角接到任务；第二幕：冲突升级；第三幕：高潮与反转。";
  form.elements.script.value = form.elements.script.value.trim() ? `${form.elements.script.value}\n${base}` : base;
  markProgress();
  refreshPreview();
});

document.getElementById("createProjectBtn").addEventListener("click", async () => {
  try {
    await createProject();
    markProgress();
  } catch (e) {
    resultBox.textContent = `创建项目失败：${e.message}`;
  }
});

document.getElementById("queueImageBtn").addEventListener("click", async () => {
  try {
    await enqueue("text_to_image");
    markProgress();
  } catch (e) {
    resultBox.textContent = `文生图入队失败：${e.message}`;
  }
});

document.getElementById("queueVideoBtn").addEventListener("click", async () => {
  try {
    await enqueue("image_to_video");
    markProgress();
  } catch (e) {
    resultBox.textContent = `图生视频入队失败：${e.message}`;
  }
});

initWorkflow();
markProgress();
refreshPreview();
startTaskSocket();
