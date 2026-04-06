from __future__ import annotations

import asyncio
import itertools
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

TaskType = Literal[
    "text_to_image",
    "image_to_video",
    "text_to_video",
    "scene_asset_generation",
    "character_asset_generation",
    "sclass_video_generation",
]
TaskStatus = Literal["queued", "running", "completed", "failed", "retrying"]


class ApiProviderCreate(BaseModel):
    name: str
    base_url: str
    keys: list[str] = Field(default_factory=list)


class RouteBindingCreate(BaseModel):
    capability: Literal["text_to_image", "image_to_video", "text_to_video", "llm"]
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
    project_name: str = Field(..., min_length=1)
    raw_script: str = Field(..., min_length=5)


class CalibrationRequest(BaseModel):
    project_id: str
    style: str = "cinematic"
    target_model: str = "sdxl"


class TaskCreateRequest(BaseModel):
    project_id: str
    task_type: TaskType
    input: dict[str, Any]


class BatchImageRequest(BaseModel):
    project_id: str
    storyboard_ids: list[str]


class BatchVideoRequest(BaseModel):
    project_id: str
    storyboard_ids: list[str]


class SClassGroup(BaseModel):
    group_name: str
    storyboard_ids: list[str]


class SClassRequest(BaseModel):
    project_id: str
    groups: list[SClassGroup]


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
            "providers": [
                {"name": name, "base_url": p.base_url, "key_count": len(p.keys)}
                for name, p in self.api_providers.items()
            ],
            "routes": [
                {
                    "capability": capability,
                    "provider": binding.provider_name,
                    "model": binding.model,
                }
                for capability, binding in self.route_bindings.items()
            ],
            "image_bed": {
                "provider": self.image_bed.provider if self.image_bed else None,
                "key_count": len(self.image_bed.keys) if self.image_bed else 0,
                "endpoint": self.image_bed.endpoint if self.image_bed else None,
            },
        }

    def select_provider_key(self, capability: str) -> dict[str, str]:
        route = self.route_bindings.get(capability)
        if not route:
            raise ValueError(f"route for capability '{capability}' is not configured")
        provider = self.api_providers.get(route.provider_name)
        if not provider:
            raise ValueError(f"provider '{route.provider_name}' is not configured")
        cycle = self.provider_cycles.get(route.provider_name)
        if cycle is None:
            raise ValueError(f"provider '{route.provider_name}' has no key cycle")
        key = next(cycle)
        return {
            "provider": route.provider_name,
            "model": route.model,
            "api_key": key,
            "base_url": provider.base_url,
        }

    def select_image_bed_key(self) -> dict[str, str]:
        if self.image_bed is None or self.image_bed_cycle is None:
            raise ValueError("image bed is not configured")
        return {
            "provider": self.image_bed.provider,
            "endpoint": self.image_bed.endpoint,
            "api_key": next(self.image_bed_cycle),
        }


class TaskManager:
    def __init__(self, system_store: SystemStore) -> None:
        self.system_store = system_store
        self.tasks: dict[str, Task] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.connections: set[WebSocket] = set()

    async def create_task(self, payload: TaskCreateRequest) -> Task:
        task = Task(
            id=str(uuid.uuid4()),
            project_id=payload.project_id,
            task_type=payload.task_type,
            input=payload.input,
        )
        self.tasks[task.id] = task
        await self.queue.put(task.id)
        await self.broadcast({"event": "task_created", "task": self.serialize(task)})
        return task

    async def worker_loop(self) -> None:
        while True:
            task_id = await self.queue.get()
            task = self.tasks.get(task_id)
            if task is None:
                self.queue.task_done()
                continue

            try:
                await self.run_task(task)
            except Exception as exc:  # noqa: BLE001
                await self.handle_failure(task, str(exc))
            finally:
                self.queue.task_done()

    async def run_task(self, task: Task) -> None:
        task.status = "running"
        task.attempt += 1
        task.message = f"running attempt {task.attempt}"
        task.updated_at = time.time()
        await self.broadcast({"event": "task_started", "task": self.serialize(task)})

        capability = self.map_task_to_capability(task.task_type)
        provider = self.system_store.select_provider_key(capability)

        for p in [10, 30, 55, 75, 100]:
            await asyncio.sleep(0.3)
            task.progress = p
            task.message = f"{provider['provider']}::{provider['model']} processing {p}%"
            task.updated_at = time.time()
            await self.broadcast({"event": "task_progress", "task": self.serialize(task)})

        task.status = "completed"
        task.result = self.make_result(task, provider)
        task.message = "completed"
        task.updated_at = time.time()
        await self.broadcast({"event": "task_completed", "task": self.serialize(task)})

    async def handle_failure(self, task: Task, error: str) -> None:
        if task.attempt <= task.max_retries:
            task.status = "retrying"
            task.message = f"{error}; retry scheduled"
            task.updated_at = time.time()
            await self.broadcast({"event": "task_retrying", "task": self.serialize(task)})
            await self.queue.put(task.id)
            return

        task.status = "failed"
        task.message = error
        task.updated_at = time.time()
        await self.broadcast({"event": "task_failed", "task": self.serialize(task)})

    @staticmethod
    def map_task_to_capability(task_type: TaskType) -> str:
        if task_type in {"text_to_image", "scene_asset_generation", "character_asset_generation"}:
            return "text_to_image"
        if task_type in {"image_to_video", "sclass_video_generation"}:
            return "image_to_video"
        return "text_to_video"

    def make_result(self, task: Task, provider: dict[str, str]) -> dict[str, Any]:
        image_bed = self.system_store.select_image_bed_key()
        ext = "png" if "image" in task.task_type else "mp4"
        asset_url = f"https://cdn.example.com/{task.project_id}/{task.id}.{ext}"
        return {
            "asset_url": asset_url,
            "provider": provider["provider"],
            "model": provider["model"],
            "image_bed": image_bed["provider"],
            "upload_endpoint": image_bed["endpoint"],
            "meta": task.input,
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
            "attempt": task.attempt,
            "max_retries": task.max_retries,
            "message": task.message,
            "result": task.result,
            "input": task.input,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        }


def parse_script(raw_script: str) -> dict[str, Any]:
    scenes: list[dict[str, Any]] = []
    characters: dict[str, dict[str, Any]] = {}
    storyboards: list[dict[str, Any]] = []
    dialogues: list[dict[str, Any]] = []

    lines = [x.strip() for x in raw_script.splitlines() if x.strip()]
    current_scene = "默认场景"
    scene_index = 0

    for line in lines:
        if line.startswith(("场景", "Scene")):
            scene_index += 1
            current_scene = line
            scenes.append({"id": f"scn_{scene_index}", "title": line, "description": line})
            continue

        if ":" in line:
            speaker, text = [x.strip() for x in line.split(":", 1)]
            char_id = f"chr_{speaker}"
            characters.setdefault(
                char_id,
                {
                    "id": char_id,
                    "name": speaker,
                    "consistency_anchor": f"{speaker}:发型固定+主色服饰+标志动作",
                },
            )
            dialogues.append(
                {
                    "id": str(uuid.uuid4()),
                    "scene": current_scene,
                    "speaker": speaker,
                    "text": text,
                }
            )

        sb_id = f"sb_{len(storyboards)+1}"
        storyboards.append(
            {
                "id": sb_id,
                "scene": current_scene,
                "raw_line": line,
                "shot": "中景",
                "camera": "平稳推进",
                "composition": "三分法",
            }
        )

    if not scenes:
        scenes = [{"id": "scn_1", "title": "默认场景", "description": "自动补全场景"}]

    return {
        "scenes": scenes,
        "characters": list(characters.values()),
        "storyboards": storyboards,
        "dialogues": dialogues,
    }


def calibrate_project(parsed: dict[str, Any], style: str, model: str) -> dict[str, Any]:
    scenes = []
    for s in parsed.get("scenes", []):
        scenes.append(
            {
                **s,
                "calibrated_prompt": f"[{style}] {s['description']}，电影级光影、环境层次、体积雾、{model}友好词法",
            }
        )

    storyboards = []
    for sb in parsed.get("storyboards", []):
        storyboards.append(
            {
                **sb,
                "first_frame_prompt": f"{sb['scene']} {sb['raw_line']}，{sb['shot']}，{sb['composition']}",
                "last_frame_prompt": f"动作收束镜头，延续{sb['camera']}，保持角色一致性",
                "video_prompt": f"动作表现+镜头语言+对白唇形同步：{sb['raw_line']}",
            }
        )

    characters = []
    for c in parsed.get("characters", []):
        characters.append(
            {
                **c,
                "calibrated_prompt": f"{c['name']}，外观一致性锚点：{c['consistency_anchor']}，多角度表情和动作模板",
            }
        )

    return {
        "scene_calibration": scenes,
        "shot_calibration": storyboards,
        "character_calibration": characters,
    }


app = FastAPI(title="AI Video Workspace API", version="0.4.0")
CORS_ORIGINS = [x.strip() for x in os.getenv("AI_VIDEO_WORKSPACE_CORS", "*").split(",") if x.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

system_store = SystemStore()
manager = TaskManager(system_store)
PROJECTS: dict[str, dict[str, Any]] = {}


@app.on_event("startup")
async def startup() -> None:
    asyncio.create_task(manager.worker_loop())


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "queued": manager.queue.qsize(),
        "task_count": len(manager.tasks),
        "project_count": len(PROJECTS),
        "settings": system_store.summary(),
    }


@app.post("/api/settings")
async def save_settings(payload: SystemSettingsRequest) -> dict[str, Any]:
    return system_store.configure(payload)


@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    return system_store.summary()


@app.post("/api/script/import")
async def import_script(payload: ScriptInputRequest) -> dict[str, Any]:
    project_id = str(uuid.uuid4())
    parsed = parse_script(payload.raw_script)
    PROJECTS[project_id] = {
        "id": project_id,
        "project_name": payload.project_name,
        "raw_script": payload.raw_script,
        "parsed": parsed,
        "calibration": None,
        "created_at": time.time(),
    }
    return PROJECTS[project_id]


@app.post("/api/calibration")
async def run_calibration(payload: CalibrationRequest) -> dict[str, Any]:
    project = PROJECTS.get(payload.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    calibration = calibrate_project(project["parsed"], payload.style, payload.target_model)
    project["calibration"] = calibration
    return calibration


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str) -> dict[str, Any]:
    project = PROJECTS.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    return project


@app.post("/api/assets/scene")
async def generate_scene_assets(payload: TaskCreateRequest) -> dict[str, Any]:
    task = await manager.create_task(
        TaskCreateRequest(
            project_id=payload.project_id,
            task_type="scene_asset_generation",
            input=payload.input,
        )
    )
    return manager.serialize(task)


@app.post("/api/assets/character")
async def generate_character_assets(payload: TaskCreateRequest) -> dict[str, Any]:
    task = await manager.create_task(
        TaskCreateRequest(
            project_id=payload.project_id,
            task_type="character_asset_generation",
            input=payload.input,
        )
    )
    return manager.serialize(task)


@app.post("/api/director/sync")
async def director_sync(project_id: str) -> dict[str, Any]:
    project = PROJECTS.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    return {
        "project_id": project_id,
        "storyboards": (project.get("calibration") or {}).get("shot_calibration")
        or project["parsed"]["storyboards"],
    }


@app.post("/api/director/batch-image")
async def director_batch_image(payload: BatchImageRequest) -> dict[str, Any]:
    project = PROJECTS.get(payload.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    task_ids = []
    for storyboard_id in payload.storyboard_ids:
        task = await manager.create_task(
            TaskCreateRequest(
                project_id=payload.project_id,
                task_type="text_to_image",
                input={"storyboard_id": storyboard_id, "source": "director_module"},
            )
        )
        task_ids.append(task.id)

    return {"queued": len(task_ids), "task_ids": task_ids}


@app.post("/api/director/batch-video")
async def director_batch_video(payload: BatchVideoRequest) -> dict[str, Any]:
    project = PROJECTS.get(payload.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    task_ids = []
    for storyboard_id in payload.storyboard_ids:
        task = await manager.create_task(
            TaskCreateRequest(
                project_id=payload.project_id,
                task_type="image_to_video",
                input={"storyboard_id": storyboard_id, "source": "director_module"},
            )
        )
        task_ids.append(task.id)

    return {"queued": len(task_ids), "task_ids": task_ids}


@app.post("/api/sclass/compose")
async def sclass_compose(payload: SClassRequest) -> dict[str, Any]:
    project = PROJECTS.get(payload.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    validation_errors: list[str] = []
    tasks: list[str] = []
    for group in payload.groups:
        if len(group.storyboard_ids) == 0:
            validation_errors.append(f"group '{group.group_name}' has no storyboard")
            continue
        if len(group.storyboard_ids) > 6:
            validation_errors.append(f"group '{group.group_name}' exceeds Seedance image limit")
            continue

        task = await manager.create_task(
            TaskCreateRequest(
                project_id=payload.project_id,
                task_type="sclass_video_generation",
                input={
                    "group_name": group.group_name,
                    "storyboard_ids": group.storyboard_ids,
                    "fusion_prompt": "动作表现 + 镜头语言 + 对白唇形同步",
                    "modal_refs": ["@Image", "@Video", "@Audio"],
                },
            )
        )
        tasks.append(task.id)

    return {
        "accepted_groups": len(tasks),
        "task_ids": tasks,
        "validation_errors": validation_errors,
    }


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
async def tasks_ws(ws: WebSocket) -> None:
    await ws.accept()
    manager.connections.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.connections.discard(ws)
