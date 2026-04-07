# AI Video Workspace

前端已升级为 **JavaScript 框架（React + Vite）**，工作流完整覆盖：

1. 剧本导入与解析
2. 角色 + 场景提示词校准
3. 批量生图（角色图/场景图）
4. 图再生视频（支持运镜、光影、画面特效参数）
5. 逐段视频拼接并导出成片

并支持 **前后端分离** 与 **VPS 自托管部署**。

---

## 项目结构

- `frontend/`：React + Vite Dashboard
- `backend/`：FastAPI API + 异步任务队列 + WebSocket
- `docker-compose.yml`：前后端容器编排
- `deploy/nginx/frontend.conf`：Nginx SPA + `/api` `/ws` 反向代理

## 安全登录（部署要求）

后端要求用户名密码登录后才可进入工作台调用 API：

- 环境变量：
  - `AI_VIDEO_WORKSPACE_USERNAME`
  - `AI_VIDEO_WORKSPACE_PASSWORD`
- 登录接口：`POST /api/auth/login`
- 登录成功返回 `token`，后续请求使用 `Authorization: Bearer <token>`。

WebSocket 连接使用：`/ws/tasks?token=<token>`。

---

## 本地开发

### 后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 前端（React）

```bash
cd frontend
npm install
npm run dev
```

默认访问：`http://127.0.0.1:5173`

---

## VPS 自托管部署（Docker Compose）

建议先设置环境变量：

```bash
export AI_VIDEO_WORKSPACE_USERNAME=your_admin
export AI_VIDEO_WORKSPACE_PASSWORD='your_strong_password'
```

启动：

```bash
docker compose up -d --build
```

访问：
- 前端 Dashboard：`http://<VPS-IP>:8080`
- 后端 API：`http://<VPS-IP>:8000`

---

## 生产化建议

- 队列迁移 Redis + BullMQ/Celery
- 项目/任务持久化到 PostgreSQL
- 产物上传 OSS/S3 并使用签名 URL
- 增加限流、熔断、失败任务接管
