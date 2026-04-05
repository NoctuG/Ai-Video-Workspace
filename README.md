# Moyin-style AI 创作工作台（Web MVP）

这是一个从“桌面端思路”迁移到“Web 架构”的最小可行实现，核心体现：

- **异步任务管理**：后端 `FastAPI + asyncio queue`，任务入队后秒回 `taskId`
- **状态同步**：前端通过 **WebSocket** 实时接收任务进度
- **云存储思路**：任务完成后返回 CDN/OSS 风格 URL（示例）
- **生产流**：`准备工作 -> 剧本 -> AI校准 -> 场景/角色 -> 导演/S级 -> 生成视频`

## 目录

- `index.html`：前端工作台界面
- `app.js`：前端状态编排、任务创建、WebSocket 同步
- `styles.css`：UI 样式
- `backend/main.py`：后端 API + 队列 Worker + WebSocket 事件推送
- `backend/requirements.txt`：后端依赖

## 运行

### 1) 启动后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 2) 启动前端

直接用静态服务打开根目录：

```bash
python -m http.server 5500
```

打开 `http://127.0.0.1:5500`。

## API 概览

- `POST /api/projects`：创建项目（保存工作流与表单）
- `POST /api/tasks`：创建异步任务（文生图 / 图生视频）
- `GET /api/tasks/{taskId}`：查看任务状态
- `GET /api/projects/{projectId}/tasks`：项目任务列表
- `GET /api/health`：服务健康状态
- `WS /ws/tasks`：任务进度实时推送

## 下一步（对齐生产级）

1. 用 Redis + BullMQ/Celery 替换内存队列
2. 接入 PostgreSQL 存储项目、角色圣经、密钥配置
3. 实现 AI Gateway（多模型路由/失败重试/限流）
4. 对接 OSS/S3 真正上传文件并返回签名 URL
5. 增加 ZIP 打包导出与权限系统
