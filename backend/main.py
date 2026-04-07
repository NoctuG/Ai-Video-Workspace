from __future__ import annotations

import asyncio
import itertools
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

TaskType = Literal["image_generation", "video_generation", "stitch_export"]
TaskStatus = Literal["queued", "running", "completed", "failed", "retrying"]


class LoginRequest(BaseModel):
    username: str
    password: str


class ApiProviderCreate(BaseModel):
    name: str
    base_url: str
    keys: list[str] = Field(default_factory=list)


class RouteBindingCreate(BaseModel):
    capability: Literal["image_generation", "video_generation", "llm"]
    provider_name: str
    model: str


class ImageBedCreate(BaseModel):
    provider: str
    endpoint: str
    keys: list[str] = Field(default_factory=list)


class SystemSettingsRequest(BaseModel):
    api_providers: list[ApiProviderCreate]
    routes: list[RouteBindingCreate]
    image_bed: ImageBedCreate


class ScriptInputRequest(BaseModel):
    project_name: str
    raw_script: str


class CalibrationRequest(BaseModel):
    project_id: str
    style: str = "cinematic"


class GenerateImagesRequest(BaseModel):
    project_id: str


class GenerateVideosRequest(BaseModel):
    project_id: str
    camera_motion: str = "平稳推进"
    lighting: str = "电影感光影"
    effects: list[str] = Field(default_factory=lambda: ["胶片颗粒"])


class StitchExportRequest(BaseModel):
    project_id: str
    segment_task_ids: list[str]


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
    attempt: int = 0
    max_retries: int = 2
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class AuthStore:
    def __init__(self) -> None:
        self.tokens: dict[str, str] = {}

    @staticmethod
    def expected_username() -> str:
        return os.getenv("AI_VIDEO_WORKSPACE_USERNAME", "admin")

    @staticmethod
    def expected_password() -> str:
        return os.getenv("AI_VIDEO_WORKSPACE_PASSWORD", "admin123")

    def login(self, username: str, password: str) -> str:
        if username != self.expected_username() or password != self.expected_password():
            raise HTTPException(status_code=401, detail="invalid credentials")
        token = secrets.token_urlsafe(24)
        self.tokens[token] = username
        return token

    def verify(self, token: str) -> None:
        if token not in self.tokens:
            raise HTTPException(status_code=401, detail="unauthorized")


class SystemStore:
    def __init__(self) -> None:
        self.api_providers: dict[str, ApiProviderCreate] = {}
        self.route_bindings: dict[str, RouteBindingCreate] = {}
        self.image_bed: ImageBedCreate | None = None
        self.provider_cycles: dict[str, itertools.cycle[str]] = {}
        self.image_bed_cycle: itertools.cycle[str] | None = None

    def configure(self, payload: SystemSettingsRequest) -> dict[str, Any]:
        self.api_providers = {p.name: p for p in payload.api_providers}
        self.route_bindings = {r.capability: r for r in payload.routes}
        self.image_bed = payload.image_bed
        self.provider_cycles = {
            name: itertools.cycle(provider.keys or ["<empty-key>"])
            for name, provider in self.api_providers.items()
        }
        self.image_bed_cycle = itertools.cycle(payload.image_bed.keys or ["<empty-key>"])
        return self.summary()

    def summary(self) -> dict[str, Any]:
        return {
            "providers": [{"name": n, "key_count": len(p.keys)} for n, p in self.api_providers.items()],
            "routes": [{"capability": c, "provider": r.provider_name, "model": r.model} for c, r in self.route_bindings.items()],
            "image_bed": {
                "provider": self.image_bed.provider if self.image_bed else None,
                "key_count": len(self.image_bed.keys) if self.image_bed else 0,
            },
        }

    def select_provider(self, capability: str) -> dict[str, str]:
        route = self.route_bindings.get(capability)
        if not route:
            raise ValueError(f"missing route for {capability}")
        provider = self.api_providers.get(route.provider_name)
        if not provider:
            raise ValueError("missing provider")
        key = next(self.provider_cycles[provider.name])
        return {"provider": provider.name, "model": route.model, "api_key": key}


class TaskManager:
    def __init__(self, system_store: SystemStore) -> None:
        self.system_store = system_store
        self.tasks: dict[str, Task] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.connections: set[WebSocket] = set()

    async def create_task(self, project_id: str, task_type: TaskType, input_data: dict[str, Any]) -> Task:
        task = Task(id=str(uuid.uuid4()), project_id=project_id, task_type=task_type, input=input_data)
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
                await self.fail_or_retry(task, str(exc))
            finally:
                self.queue.task_done()

    async def run_task(self, task: Task) -> None:
        task.status = "running"
        task.attempt += 1
        capability = "image_generation" if task.task_type == "image_generation" else "video_generation"
        provider = self.system_store.select_provider(capability) if capability in self.system_store.route_bindings else {"provider": "local", "model": "mock"}

        for progress in [15, 35, 55, 75, 100]:
            await asyncio.sleep(0.25)
            task.progress = progress
            task.message = f"{provider['provider']}::{provider['model']} {progress}%"
            task.updated_at = time.time()
            await self.broadcast({"event": "task_progress", "task": self.serialize(task)})

        ext = "png" if task.task_type == "image_generation" else "mp4"
        if task.task_type == "stitch_export":
            ext = "mp4"
        task.result = {
            "asset_url": f"https://cdn.example.com/{task.project_id}/{task.id}.{ext}",
            "meta": task.input,
        }
        task.status = "completed"
        task.message = "completed"
        task.updated_at = time.time()
        await self.broadcast({"event": "task_completed", "task": self.serialize(task)})

    async def fail_or_retry(self, task: Task, err: str) -> None:
        if task.attempt <= task.max_retries:
            task.status = "retrying"
            task.message = err
            await self.queue.put(task.id)
            await self.broadcast({"event": "task_retrying", "task": self.serialize(task)})
            return
        task.status = "failed"
        task.message = err
        await self.broadcast({"event": "task_failed", "task": self.serialize(task)})

    async def broadcast(self, payload: dict[str, Any]) -> None:
        broken: list[WebSocket] = []
        for conn in self.connections:
            try:
                await conn.send_json(payload)
            except Exception:  # noqa: BLE001
                broken.append(conn)
        for conn in broken:
            self.connections.discard(conn)

    @staticmethod
    def serialize(task: Task) -> dict[str, Any]:
        return {
            "id": task.id,
            "project_id": task.project_id,
            "task_type": task.task_type,
            "status": task.status,
            "progress": task.progress,
            "attempt": task.attempt,
            "message": task.message,
            "result": task.result,
            "updated_at": task.updated_at,
        }


def parse_script(raw_script: str) -> dict[str, Any]:
    scenes = []
    characters: dict[str, dict[str, Any]] = {}
    storyboards = []

    current_scene = "默认场景"
    for line in [x.strip() for x in raw_script.splitlines() if x.strip()]:
        if line.startswith(("场景", "Scene")):
            current_scene = line
            scenes.append({"id": f"scn_{len(scenes)+1}", "title": line})
            continue

        if ":" in line:
            speaker, text = [x.strip() for x in line.split(":", 1)]
            characters.setdefault(speaker, {"id": f"chr_{speaker}", "name": speaker, "anchor": f"{speaker}外观锚点"})
            storyboards.append(
                {
                    "id": f"sb_{len(storyboards)+1}",
                    "scene": current_scene,
                    "line": text,
                    "speaker": speaker,
                    "shot": "中景",
                }
            )

    if not scenes:
        scenes = [{"id": "scn_1", "title": "默认场景"}]

    return {"scenes": scenes, "characters": list(characters.values()), "storyboards": storyboards}


def calibrate(parsed: dict[str, Any], style: str) -> dict[str, Any]:
    return {
        "scene_prompts": [
            {**s, "prompt": f"{style} {s['title']}，层次化光影，环境细节丰富"} for s in parsed["scenes"]
        ],
        "character_prompts": [
            {**c, "prompt": f"{c['name']}，保持一致性锚点：{c['anchor']}"} for c in parsed["characters"]
        ],
        "storyboard_prompts": [
            {
                **sb,
                "image_prompt": f"{style} {sb['scene']} {sb['line']} {sb['shot']}",
                "video_prompt": f"动作+运镜+光影+特效：{sb['line']}",
            }
            for sb in parsed["storyboards"]
        ],
    }


app = FastAPI(title="AI Video Workspace API", version="0.5.0")
CORS_ORIGINS = [x.strip() for x in os.getenv("AI_VIDEO_WORKSPACE_CORS", "*").split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

auth_store = AuthStore()
system_store = SystemStore()
manager = TaskManager(system_store)
PROJECTS: dict[str, dict[str, Any]] = {}


def require_auth(authorization: str = Header(default="")) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    auth_store.verify(token)
    return token


@app.on_event("startup")
async def startup() -> None:
    asyncio.create_task(manager.worker_loop())


@app.post("/api/auth/login")
async def login(payload: LoginRequest) -> dict[str, str]:
    token = auth_store.login(payload.username, payload.password)
    return {"token": token}


@app.get("/api/health")
async def health(_: str = Depends(require_auth)) -> dict[str, Any]:
    return {"ok": True, "project_count": len(PROJECTS), "task_count": len(manager.tasks)}


@app.post("/api/settings")
async def save_settings(payload: SystemSettingsRequest, _: str = Depends(require_auth)) -> dict[str, Any]:
    return system_store.configure(payload)


@app.post("/api/script/import")
async def import_script(payload: ScriptInputRequest, _: str = Depends(require_auth)) -> dict[str, Any]:
    pid = str(uuid.uuid4())
    parsed = parse_script(payload.raw_script)
    PROJECTS[pid] = {
        "id": pid,
        "name": payload.project_name,
        "raw_script": payload.raw_script,
        "parsed": parsed,
        "calibration": None,
    }
    return PROJECTS[pid]


@app.post("/api/calibration")
async def run_calibration(payload: CalibrationRequest, _: str = Depends(require_auth)) -> dict[str, Any]:
    project = PROJECTS.get(payload.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    project["calibration"] = calibrate(project["parsed"], payload.style)
    return project["calibration"]


@app.post("/api/workflow/generate-images")
async def generate_images(payload: GenerateImagesRequest, _: str = Depends(require_auth)) -> dict[str, Any]:
    project = PROJECTS.get(payload.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    calibration = project.get("calibration") or calibrate(project["parsed"], "cinematic")
    task_ids = []
    for sb in calibration["storyboard_prompts"]:
        task = await manager.create_task(payload.project_id, "image_generation", {"storyboard_id": sb["id"], "prompt": sb["image_prompt"]})
        task_ids.append(task.id)
    return {"queued": len(task_ids), "task_ids": task_ids}


@app.post("/api/workflow/generate-videos")
async def generate_videos(payload: GenerateVideosRequest, _: str = Depends(require_auth)) -> dict[str, Any]:
    project = PROJECTS.get(payload.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    calibration = project.get("calibration") or calibrate(project["parsed"], "cinematic")
    task_ids = []
    for sb in calibration["storyboard_prompts"]:
        task = await manager.create_task(
            payload.project_id,
            "video_generation",
            {
                "storyboard_id": sb["id"],
                "prompt": sb["video_prompt"],
                "camera_motion": payload.camera_motion,
                "lighting": payload.lighting,
                "effects": payload.effects,
            },
        )
        task_ids.append(task.id)
    return {"queued": len(task_ids), "task_ids": task_ids}


@app.post("/api/workflow/stitch-export")
async def stitch_export(payload: StitchExportRequest, _: str = Depends(require_auth)) -> dict[str, Any]:
    if payload.project_id not in PROJECTS:
        raise HTTPException(status_code=404, detail="project not found")
    task = await manager.create_task(payload.project_id, "stitch_export", {"segments": payload.segment_task_ids})
    return {"task_id": task.id}


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str, _: str = Depends(require_auth)) -> dict[str, Any]:
    project = PROJECTS.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    return project


@app.get("/api/projects/{project_id}/tasks")
async def list_tasks(project_id: str, _: str = Depends(require_auth)) -> list[dict[str, Any]]:
    return [manager.serialize(t) for t in manager.tasks.values() if t.project_id == project_id]


@app.websocket("/ws/tasks")
async def tasks_ws(ws: WebSocket, token: str = Query(default="")) -> None:
    try:
        auth_store.verify(token)
    except HTTPException:
        await ws.close(code=4401)
        return

    await ws.accept()
    manager.connections.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.connections.discard(ws)
