# AI Video Workspace

一个前后端分离的 AI 视频工作台示例项目。

## 快速部署（Docker Compose）

### 1) 准备
```bash
git clone <your-repo-url>
cd Ai-Video-Workspace
```

### 2) 启动
```bash
docker compose up -d --build
```

### 3) 访问
- 前端：`http://<服务器IP>:8080`
- 后端：`http://<服务器IP>:8000`
- 健康检查：`http://<服务器IP>:8000/api/health`

### 4) 停止
```bash
docker compose down
```

## 可选：本地开发运行

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

访问：`http://127.0.0.1:5500`
