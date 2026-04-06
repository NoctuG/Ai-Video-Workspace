# Moyin Creator Web Studio (MVP+)

本版本根据“系统配置 -> 剧本解析 -> AI 校准 -> 资产库 -> 导演流 -> S级流”需求做了模块化实现，重点不是桌面端 Electron，而是 Web 端标准架构：

- 前端：单页控制台（可替换成 Next.js/React 版本）
- 后端：FastAPI + 异步任务队列 + WebSocket
- 存储思路：云端图床/对象存储 URL（示例）

## 已实现模块

1. **系统与基础设施配置**
   - `POST /api/settings` 保存 API 服务商、路由映射、图床配置
   - 多 key 轮询（provider key cycle / image bed key cycle）
2. **剧本导入与智能解析**
   - `POST /api/script/import` 导入原始剧本
   - 自动拆解为 scenes / storyboards / characters / dialogues
3. **AI 校准引擎**
   - `POST /api/calibration` 输出场景、分镜、角色三类校准 prompt
4. **素材库生成（可选）**
   - `POST /api/assets/scene`
   - `POST /api/assets/character`
5. **导演分镜工作流**
   - `POST /api/director/sync`
   - `POST /api/director/batch-image`
   - `POST /api/director/batch-video`
6. **S级多模态创作流**
   - `POST /api/sclass/compose`
   - 支持分组校验与任务下发

## 非功能性能力（MVP 范围）

- 任务异步化：任务秒回 + Worker 后台处理
- 并行/高并发基础：队列模型 + 多任务入队
- 稳定性基础：失败自动重试（max_retries）
- 数据连贯性：项目结构化状态贯穿全流程

## 启动方式

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

另开终端：

```bash
python -m http.server 5500
```

访问：`http://127.0.0.1:5500`

## 后续建议（生产化）

- 队列：替换为 Redis + BullMQ/Celery
- 存储：接入 PostgreSQL 持久化项目和任务
- 文件：接入 OSS/S3 并提供签名下载链接
- 网关：增加 provider 熔断、限流、失败接管
