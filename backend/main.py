from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

TaskType = Literal["text_to_image", "image_to_video"]
TaskStatus = Literal["queued", "running", "completed", "failed"]


class ScriptRequest(BaseModel):
    project_name: str = Field(..., min_length=1)
    workflow: list[str]
    payload: dict[str, Any]


class TaskCreateRequest(BaseModel):
    project_id: str
    task_type: TaskType
    input: dict[str, Any]


@dataclass
class Task:
    id: str
    project_id: str
    task_type: TaskType
    input: dict[str, Any]
    status: TaskStatus = "queued"
    progress: int = 0
    message: str = "waiting"
    result: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class TaskManager:
    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.connections: set[WebSocket] = set()

    async def create_task(self, data: TaskCreateRequest) -> Task:
        task = Task(
            id=str(uuid.uuid4()),
            project_id=data.project_id,
            task_type=data.task_type,
            input=data.input,
        )
        self.tasks[task.id] = task
        await self.queue.put(task.id)
        await self.broadcast({"event": "task_created", "task": self.serialize(task)})
        return task

    async def worker_loop(self) -> None:
        while True:
            task_id = await self.queue.get()
            task = self.tasks.get(task_id)
            if not task:
                self.queue.task_done()
                continue
            try:
                await self.run_task(task)
            except Exception as exc:  # noqa: BLE001
                task.status = "failed"
                task.message = str(exc)
                task.updated_at = time.time()
                await self.broadcast({"event": "task_failed", "task": self.serialize(task)})
            finally:
                self.queue.task_done()

    async def run_task(self, task: Task) -> None:
        task.status = "running"
        task.message = "accepted by worker"
        task.updated_at = time.time()
        await self.broadcast({"event": "task_started", "task": self.serialize(task)})

        for p in [15, 35, 60, 85, 100]:
            await asyncio.sleep(0.6)
            task.progress = p
            task.message = f"processing {p}%"
            task.updated_at = time.time()
            await self.broadcast({"event": "task_progress", "task": self.serialize(task)})

        task.status = "completed"
        task.result = self.make_result(task)
        task.message = "done"
        task.updated_at = time.time()
        await self.broadcast({"event": "task_completed", "task": self.serialize(task)})

    def make_result(self, task: Task) -> dict[str, Any]:
        if task.task_type == "text_to_image":
            # Demo URL: in production this should be OSS/CDN URL
            return {
                "asset_type": "image",
                "url": f"https://cdn.example.com/{task.project_id}/{task.id}.png",
                "prompt": task.input.get("prompt", ""),
            }
        return {
            "asset_type": "video",
            "url": f"https://cdn.example.com/{task.project_id}/{task.id}.mp4",
            "duration_seconds": task.input.get("duration_seconds", 8),
        }

    async def broadcast(self, payload: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for conn in self.connections:
            try:
                await conn.send_json(payload)
            except Exception:  # noqa: BLE001
                dead.append(conn)
        for conn in dead:
            self.connections.discard(conn)

    @staticmethod
    def serialize(task: Task) -> dict[str, Any]:
        return {
            "id": task.id,
            "project_id": task.project_id,
            "task_type": task.task_type,
            "status": task.status,
            "progress": task.progress,
            "message": task.message,
            "result": task.result,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "input": task.input,
        }


app = FastAPI(title="AI Video Workflow API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

manager = TaskManager()
PROJECTS: dict[str, dict[str, Any]] = {}


@app.on_event("startup")
async def startup() -> None:
    asyncio.create_task(manager.worker_loop())


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "queued": manager.queue.qsize(), "tasks": len(manager.tasks)}


@app.post("/api/projects")
async def create_project(payload: ScriptRequest) -> dict[str, Any]:
    project_id = str(uuid.uuid4())
    PROJECTS[project_id] = {
        "id": project_id,
        "project_name": payload.project_name,
        "workflow": payload.workflow,
        "payload": payload.payload,
        "created_at": time.time(),
    }
    return PROJECTS[project_id]


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str) -> dict[str, Any]:
    if project_id not in PROJECTS:
        raise HTTPException(status_code=404, detail="project not found")
    return PROJECTS[project_id]


@app.post("/api/tasks")
async def create_task(payload: TaskCreateRequest) -> dict[str, Any]:
    if payload.project_id not in PROJECTS:
        raise HTTPException(status_code=404, detail="project not found")
    task = await manager.create_task(payload)
    return manager.serialize(task)


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    task = manager.tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return manager.serialize(task)


@app.get("/api/projects/{project_id}/tasks")
async def list_project_tasks(project_id: str) -> list[dict[str, Any]]:
    return [manager.serialize(t) for t in manager.tasks.values() if t.project_id == project_id]


@app.websocket("/ws/tasks")
async def task_ws(ws: WebSocket) -> None:
    await ws.accept()
    manager.connections.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.connections.discard(ws)
