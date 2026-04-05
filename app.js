const WORKFLOW = [
  "准备工作",
  "剧本",
  "AI校准",
  "场景/角色（可选）",
  "导演/S级",
  "生成视频",
];

const form = document.getElementById("studioForm");
const workflowList = document.getElementById("workflowList");
const requestPreview = document.getElementById("requestPreview");
const preview = document.getElementById("preview");
const imageUpload = document.getElementById("imageUpload");

function bootstrapWorkflow() {
  workflowList.innerHTML = WORKFLOW.map((x) => `<li>${x}</li>`).join("");
}

function asObject() {
  const data = Object.fromEntries(new FormData(form).entries());
  data.creativity = Number(data.creativity || 6);
  data.shotCount = Number(data.shotCount || 4);
  data.sTier = !!data.sTier;
  return data;
}

function updateProgressAndPreview() {
  const data = asObject();
  const done = {
    "准备工作": !!data.projectName,
    "剧本": !!data.script,
    "AI校准": !!(data.styleKeywords || data.negativePrompt),
    "场景/角色（可选）": !!(data.scenes || data.characters),
    "导演/S级": !!data.cameraDirection,
    "生成视频": false,
  };

  [...workflowList.querySelectorAll("li")].forEach((li) => {
    li.classList.toggle("done", done[li.textContent]);
  });

  const imageName = imageUpload.files[0]?.name || null;
  requestPreview.textContent = JSON.stringify(
    {
      pipeline: "text-to-image + image-to-video",
      workflow: WORKFLOW,
      payload: data,
      upload: imageName,
      adapters: {
        textToImageEndpoint: "/api/text-to-image",
        imageToVideoEndpoint: "/api/image-to-video",
      },
    },
    null,
    2
  );
}

document.getElementById("expandScript").addEventListener("click", () => {
  const script = form.elements.script.value.trim();
  if (!script) {
    form.elements.script.value = "第一幕：主角在雨夜街头发现神秘线索。\n第二幕：追逐与冲突升级。\n第三幕：真相揭晓并完成反转。";
  } else {
    form.elements.script.value = `${script}\n\n[AI建议]\n- 增加一个情绪高潮镜头\n- 在结尾加入品牌或主题落点`;
  }
  updateProgressAndPreview();
});

document.getElementById("generateImageBtn").addEventListener("click", async () => {
  const data = asObject();
  const prompt = [data.script, data.styleKeywords, data.scenes, data.characters]
    .filter(Boolean)
    .join("，");

  preview.innerHTML = `<span>正在文生图...</span>`;

  // Demo: 使用 SVG data URL 模拟文生图结果，实际可替换为后端 API
  const svg = encodeURIComponent(`<svg xmlns='http://www.w3.org/2000/svg' width='1024' height='576'>
    <defs><linearGradient id='g' x1='0' x2='1'><stop stop-color='#2f4f7f'/><stop offset='1' stop-color='#7f2f6f'/></linearGradient></defs>
    <rect width='100%' height='100%' fill='url(#g)'/>
    <text x='50%' y='46%' fill='white' font-size='36' text-anchor='middle'>文生图预览</text>
    <text x='50%' y='54%' fill='#d8e2ff' font-size='20' text-anchor='middle'>${(prompt || "请先填写剧本").slice(0, 60)}</text>
  </svg>`);
  const url = `data:image/svg+xml;charset=utf-8,${svg}`;

  preview.innerHTML = `<img src='${url}' alt='generated' />`;
  preview.dataset.generatedImage = url;
  updateProgressAndPreview();
});

document.getElementById("generateVideoBtn").addEventListener("click", async () => {
  const data = asObject();
  const hasImage = preview.dataset.generatedImage || imageUpload.files[0];
  if (!hasImage) {
    preview.textContent = "请先执行文生图或上传一张参考图。";
    return;
  }

  preview.innerHTML = `<div>正在图生视频...<br/>（示例版本：展示生成任务参数）</div>`;
  setTimeout(() => {
    preview.innerHTML = `<div>
      ✅ 已创建视频任务<br/>
      项目：${data.projectName || "未命名"}<br/>
      时长：约 ${Math.max(5, data.shotCount * 2)} 秒<br/>
      建议：将 /api/image-to-video 对接到真实模型服务
    </div>`;

    [...workflowList.querySelectorAll("li")].forEach((li) => {
      if (li.textContent === "生成视频") li.classList.add("done");
    });
  }, 1000);
});

form.addEventListener("input", updateProgressAndPreview);
imageUpload.addEventListener("change", updateProgressAndPreview);

document.getElementById("saveBtn").addEventListener("click", () => {
  localStorage.setItem("ai-video-studio-draft", JSON.stringify(asObject()));
  preview.textContent = "草稿已保存到浏览器本地。";
});

document.getElementById("loadBtn").addEventListener("click", () => {
  const raw = localStorage.getItem("ai-video-studio-draft");
  if (!raw) {
    preview.textContent = "未找到草稿。";
    return;
  }
  const data = JSON.parse(raw);
  Object.keys(data).forEach((k) => {
    if (form.elements[k]) {
      if (form.elements[k].type === "checkbox") form.elements[k].checked = !!data[k];
      else form.elements[k].value = data[k];
    }
  });
  updateProgressAndPreview();
  preview.textContent = "草稿已加载。";
});

document.getElementById("resetBtn").addEventListener("click", () => {
  form.reset();
  preview.textContent = "暂无生成结果";
  preview.removeAttribute("data-generated-image");
  updateProgressAndPreview();
});

bootstrapWorkflow();
updateProgressAndPreview();
