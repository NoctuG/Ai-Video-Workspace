# AI Video Workspace

`AI Video Workspace` 是一个支持 **前后端分离**、可 **VPS 自托管部署** 的 AI 视频创作工作台（MVP+）。

## 架构

- `frontend/`：纯前端控制台（静态资源，可由 Nginx 托管）
- `backend/`：FastAPI API 服务（异步任务队列 + WebSocket）
- `deploy/nginx/frontend.conf`：前端反向代理 `/api`、`/ws` 到后端
- `docker-compose.yml`：一键部署前后端容器

## 功能模块

1. 系统配置：服务商、路由、图床、多 Key 轮询
2. 剧本导入与智能拆解：Scene / Storyboard / Character / Dialogue
3. AI 校准：场景/分镜/角色提示词深化
4. 素材库（可选）：场景图/角色图生成任务
5. 导演工作流：分镜同步、批量生图、批量生视频
6. S级工作流：分镜分组、参数校验、一键生成任务

## 本地开发（前后端分离）

### 后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 前端

```bash
cd frontend
python -m http.server 5500
```

打开：`http://127.0.0.1:5500`

> 前端会读取 `frontend/config.js` 中的 API_BASE。

## VPS 自托管（推荐 Docker Compose）

```bash
git clone <your-repo-url>
cd Ai-Video-Workspace
docker compose up -d --build
```

启动后：
- 前端：`http://<VPS-IP>:8080`
- 后端：`http://<VPS-IP>:8000`

## VPS 纯 Nginx + Systemd（可选）

1. 使用 systemd 启动 `uvicorn main:app`（后端）
2. 使用 Nginx 托管 `frontend/` 静态文件
3. Nginx 反代 `/api/*` 和 `/ws/*` 到后端服务
4. 配置域名与 HTTPS（Let's Encrypt）

## 环境变量

- `AI_VIDEO_WORKSPACE_CORS`：后端 CORS 白名单（逗号分隔）
  - 例如：`http://localhost:8080,http://127.0.0.1:8080`

## 生产化建议

- 队列迁移到 Redis + BullMQ/Celery
- 数据持久化到 PostgreSQL
- 文件存储迁移 OSS/S3（签名上传/下载）
- 增加 provider 熔断、限流、失败接管
