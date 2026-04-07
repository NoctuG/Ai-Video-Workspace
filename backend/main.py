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


class ProjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    style_prompt: str = "cinematic short drama"
    style_lock: bool = True
    base_seed: int = 42


class ProjectUpdateRequest(BaseModel):
    name: str | None = None
    style_prompt: str | None = None
    style_lock: bool | None = None
    base_seed: int | None = None


class ChapterCreateRequest(BaseModel):
    title: str = Field(..., min_length=1)
    script: str = Field(..., min_length=5)


class ShotBatchUpdateRequest(BaseModel):
    shot_ids: list[str]
    patch: dict[str, Any]


class ShotGenerateRequest(BaseModel):
    model: str = "seedance-1.5"
    duration_sec: int = 5
    reference_asset_ids: list[str] = Field(default_factory=list)
    controlnet_pose: bool = False
    controlnet_depth: bool = False
    lip_sync: bool = False


class TimelineSaveRequest(BaseModel):
    clips: list[dict[str, Any]] = Field(default_factory=list)
    bgm_url: str | None = None


class MultiTrackTimelineSaveRequest(BaseModel):
    video_tracks: list[list[dict[str, Any]]] = Field(default_factory=list)
    audio_tracks: list[list[dict[str, Any]]] = Field(default_factory=list)


class PromptTemplateCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    category: Literal["storyboard", "character", "scene", "video", "music", "sfx", "composite"]
    body: str = Field(..., min_length=3)
    scope: Literal["project", "global"] = "project"
    project_id: str | None = None


class ChapterTemplateInitRequest(BaseModel):
    template_id: str


class AssetLibraryCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    type: Literal["character", "scene", "prop", "costume"]
    scope: Literal["project", "global"] = "project"
    project_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    prompt_template: str | None = None


class ModelProviderCreateRequest(BaseModel):
    provider: str = Field(..., min_length=1)
    model_type: Literal["text", "image", "video", "audio"]
    model_name: str = Field(..., min_length=1)
    is_default: bool = False


class ModelTestRequest(BaseModel):
    provider: str
    model_name: str


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


def simplify_script(raw_script: str) -> str:
    lines = [x.strip() for x in raw_script.splitlines() if x.strip()]
    compact = []
    for line in lines[:20]:
        compact.append(line.replace("：", ":"))
    return "\n".join(compact)


def extract_shots(project: dict[str, Any], chapter_id: str, script: str) -> list[dict[str, Any]]:
    lines = [x.strip() for x in script.splitlines() if x.strip()]
    shots: list[dict[str, Any]] = []
    for idx, line in enumerate(lines, start=1):
        shot_id = str(uuid.uuid4())
        shot = {
            "id": shot_id,
            "chapter_id": chapter_id,
            "order": idx,
            "content": line,
            "camera_size": "中景",
            "camera_angle": "平视",
            "camera_move": "轻推",
            "emotion": "紧张",
            "duration_sec": 5,
            "atmosphere": "电影感",
            "dialogue": line.split(":", 1)[1].strip() if ":" in line else "",
            "music": "dramatic",
            "sfx": "rain",
            "hidden": False,
            "prompts": {
                "start": f"开场定格，{line}",
                "keyframe": f"关键动作，{line}",
                "end": f"动作收束，{line}",
            },
            "versions": [{"version": 1, "created_at": time.time()}],
            "current_version": 1,
            "version_snapshots": {
                "1": {
                    "content": line,
                    "duration_sec": 5,
                    "emotion": "紧张",
                }
            },
            "latest_asset_id": None,
            "seed": project.get("base_seed", 42) + idx,
            "advanced_controls": {
                "reference_asset_ids": [],
                "controlnet_pose": False,
                "controlnet_depth": False,
                "lip_sync": False,
            },
        }
        shots.append(shot)
    return shots


app = FastAPI(title="AI Video Workspace API", version="0.4.0")
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
CHAPTERS: dict[str, dict[str, Any]] = {}
SHOTS: dict[str, dict[str, Any]] = {}
GENERATED_ASSETS: dict[str, dict[str, Any]] = {}
TIMELINES: dict[str, dict[str, Any]] = {}
PROMPT_TEMPLATES: dict[str, dict[str, Any]] = {}
ASSET_LIBRARY: dict[str, dict[str, Any]] = {}
MODEL_REGISTRY: dict[str, list[dict[str, Any]]] = {
    "text": [],
    "image": [],
    "video": [],
    "audio": [],
}


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


@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    return system_store.summary()


@app.post("/api/projects")
async def create_project(payload: ProjectCreateRequest) -> dict[str, Any]:
    project_id = str(uuid.uuid4())
    project = {
        "id": project_id,
        "name": payload.name,
        "style_prompt": payload.style_prompt,
        "style_lock": payload.style_lock,
        "base_seed": payload.base_seed,
        "status": "draft",
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    PROJECTS[project_id] = project
    return project


@app.patch("/api/projects/{project_id}")
async def update_project(project_id: str, payload: ProjectUpdateRequest) -> dict[str, Any]:
    project = PROJECTS.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    for k, v in payload.model_dump(exclude_none=True).items():
        project[k] = v
    project["updated_at"] = time.time()
    return project


@app.get("/api/projects/{project_id}/dashboard")
async def project_dashboard(project_id: str) -> dict[str, Any]:
    project = PROJECTS.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    chapters = [x for x in CHAPTERS.values() if x["project_id"] == project_id]
    shots = [x for x in SHOTS.values() if x["project_id"] == project_id]
    completed_shots = [x for x in shots if x.get("latest_asset_id")]
    return {
        "project": project,
        "chapter_count": len(chapters),
        "shot_count": len(shots),
        "generated_count": len(completed_shots),
    }


@app.post("/api/projects/{project_id}/chapters")
async def create_chapter(project_id: str, payload: ChapterCreateRequest) -> dict[str, Any]:
    project = PROJECTS.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    chapter_id = str(uuid.uuid4())
    simplified_script = simplify_script(payload.script)
    shots = extract_shots(project, chapter_id, simplified_script)
    chapter = {
        "id": chapter_id,
        "project_id": project_id,
        "title": payload.title,
        "script_raw": payload.script,
        "script_simplified": simplified_script,
        "shot_ids": [x["id"] for x in shots],
        "status": "ready",
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    CHAPTERS[chapter_id] = chapter
    for shot in shots:
        SHOTS[shot["id"]] = {**shot, "project_id": project_id}
    return {"chapter": chapter, "shots": shots}


@app.get("/api/chapters/{chapter_id}")
async def get_chapter(chapter_id: str) -> dict[str, Any]:
    chapter = CHAPTERS.get(chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="chapter not found")
    shots = [SHOTS[x] for x in chapter["shot_ids"] if x in SHOTS]
    return {"chapter": chapter, "shots": shots}


@app.patch("/api/shots/{shot_id}")
async def update_shot(shot_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    shot = SHOTS.get(shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="shot not found")
    shot.update(patch)
    return shot


@app.post("/api/shots/batch-update")
async def batch_update_shots(payload: ShotBatchUpdateRequest) -> dict[str, Any]:
    updated = 0
    for shot_id in payload.shot_ids:
        shot = SHOTS.get(shot_id)
        if not shot:
            continue
        shot.update(payload.patch)
        updated += 1
    return {"updated": updated}


@app.post("/api/shots/{shot_id}/versions")
async def create_shot_version(shot_id: str) -> dict[str, Any]:
    shot = SHOTS.get(shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="shot not found")
    next_version = int(shot["current_version"]) + 1
    shot["current_version"] = next_version
    shot["versions"].append({"version": next_version, "created_at": time.time()})
    shot["version_snapshots"][str(next_version)] = {
        "content": shot["content"],
        "duration_sec": shot["duration_sec"],
        "emotion": shot["emotion"],
    }
    return shot


@app.post("/api/shots/{shot_id}/rollback/{version}")
async def rollback_shot_version(shot_id: str, version: int) -> dict[str, Any]:
    shot = SHOTS.get(shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="shot not found")
    snapshot = shot.get("version_snapshots", {}).get(str(version))
    if not snapshot:
        raise HTTPException(status_code=404, detail="version not found")
    shot.update(snapshot)
    shot["current_version"] = version
    return shot


@app.post("/api/shots/{shot_id}/generate")
async def generate_shot_video(shot_id: str, payload: ShotGenerateRequest) -> dict[str, Any]:
    shot = SHOTS.get(shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="shot not found")
    task = await manager.create_task(
        TaskCreateRequest(
            project_id=shot["project_id"],
            task_type="text_to_video",
            input={
                "shot_id": shot_id,
                "model": payload.model,
                "duration_sec": payload.duration_sec,
                "prompt": shot["content"],
                "reference_asset_ids": payload.reference_asset_ids,
                "controlnet_pose": payload.controlnet_pose,
                "controlnet_depth": payload.controlnet_depth,
                "lip_sync": payload.lip_sync,
            },
        )
    )
    asset_id = str(uuid.uuid4())
    GENERATED_ASSETS[asset_id] = {
        "id": asset_id,
        "project_id": shot["project_id"],
        "chapter_id": shot["chapter_id"],
        "shot_id": shot_id,
        "type": "video",
        "url": f"https://cdn.example.com/{shot['project_id']}/{asset_id}.mp4",
        "quality": "draft",
        "tags": ["p0", "shot"],
        "created_at": time.time(),
        "task_id": task.id,
        "controls": {
            "reference_asset_ids": payload.reference_asset_ids,
            "controlnet_pose": payload.controlnet_pose,
            "controlnet_depth": payload.controlnet_depth,
            "lip_sync": payload.lip_sync,
        },
    }
    shot["advanced_controls"] = GENERATED_ASSETS[asset_id]["controls"]
    shot["latest_asset_id"] = asset_id
    return {"task": manager.serialize(task), "asset": GENERATED_ASSETS[asset_id]}


@app.get("/api/projects/{project_id}/generated-assets")
async def list_generated_assets(project_id: str) -> list[dict[str, Any]]:
    return [x for x in GENERATED_ASSETS.values() if x["project_id"] == project_id]


@app.post("/api/projects/{project_id}/timeline")
async def save_timeline(project_id: str, payload: TimelineSaveRequest) -> dict[str, Any]:
    if project_id not in PROJECTS:
        raise HTTPException(status_code=404, detail="project not found")
    timeline = {
        "project_id": project_id,
        "clips": payload.clips,
        "video_tracks": [payload.clips],
        "audio_tracks": [[{"kind": "bgm", "url": payload.bgm_url}] if payload.bgm_url else []],
        "bgm_url": payload.bgm_url,
        "updated_at": time.time(),
    }
    TIMELINES[project_id] = timeline
    return timeline


@app.get("/api/projects/{project_id}/timeline")
async def get_timeline(project_id: str) -> dict[str, Any]:
    return TIMELINES.get(
        project_id,
        {"project_id": project_id, "clips": [], "video_tracks": [], "audio_tracks": [], "bgm_url": None},
    )


@app.post("/api/projects/{project_id}/timeline/multitrack")
async def save_multitrack_timeline(project_id: str, payload: MultiTrackTimelineSaveRequest) -> dict[str, Any]:
    if project_id not in PROJECTS:
        raise HTTPException(status_code=404, detail="project not found")
    timeline = TIMELINES.get(project_id, {"project_id": project_id, "clips": [], "bgm_url": None})
    timeline["video_tracks"] = payload.video_tracks
    timeline["audio_tracks"] = payload.audio_tracks
    timeline["updated_at"] = time.time()
    TIMELINES[project_id] = timeline
    return timeline


@app.post("/api/templates")
async def create_prompt_template(payload: PromptTemplateCreateRequest) -> dict[str, Any]:
    template_id = str(uuid.uuid4())
    if payload.scope == "project" and not payload.project_id:
        raise HTTPException(status_code=400, detail="project scope template requires project_id")
    template = {
        "id": template_id,
        **payload.model_dump(),
        "created_at": time.time(),
    }
    PROMPT_TEMPLATES[template_id] = template
    return template


@app.get("/api/templates")
async def list_prompt_templates(project_id: str | None = None) -> list[dict[str, Any]]:
    templates = list(PROMPT_TEMPLATES.values())
    if project_id:
        return [x for x in templates if x["scope"] == "global" or x.get("project_id") == project_id]
    return templates


@app.post("/api/chapters/{chapter_id}/init-from-template")
async def init_chapter_from_template(chapter_id: str, payload: ChapterTemplateInitRequest) -> dict[str, Any]:
    chapter = CHAPTERS.get(chapter_id)
    template = PROMPT_TEMPLATES.get(payload.template_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="chapter not found")
    if not template:
        raise HTTPException(status_code=404, detail="template not found")
    for shot_id in chapter["shot_ids"]:
        shot = SHOTS.get(shot_id)
        if not shot:
            continue
        shot["prompts"]["keyframe"] = f"{template['body']} | {shot['content']}"
    return {"chapter_id": chapter_id, "template_id": payload.template_id, "updated_shots": len(chapter["shot_ids"])}


@app.post("/api/assets/library")
async def create_asset_library_item(payload: AssetLibraryCreateRequest) -> dict[str, Any]:
    if payload.scope == "project" and not payload.project_id:
        raise HTTPException(status_code=400, detail="project scope asset requires project_id")
    asset_id = str(uuid.uuid4())
    asset = {"id": asset_id, **payload.model_dump(), "created_at": time.time()}
    ASSET_LIBRARY[asset_id] = asset
    return asset


@app.get("/api/assets/library")
async def list_asset_library(project_id: str | None = None) -> list[dict[str, Any]]:
    values = list(ASSET_LIBRARY.values())
    if project_id:
        return [x for x in values if x["scope"] == "global" or x.get("project_id") == project_id]
    return values


@app.post("/api/models/providers")
async def create_model_provider(payload: ModelProviderCreateRequest) -> dict[str, Any]:
    bucket = MODEL_REGISTRY[payload.model_type]
    item = {
        "provider": payload.provider,
        "model_name": payload.model_name,
        "model_type": payload.model_type,
        "is_default": payload.is_default,
        "created_at": time.time(),
    }
    if payload.is_default:
        for model in bucket:
            model["is_default"] = False
    bucket.append(item)
    return item


@app.get("/api/models/providers")
async def list_model_providers() -> dict[str, Any]:
    return MODEL_REGISTRY


@app.post("/api/models/test")
async def test_model_provider(payload: ModelTestRequest) -> dict[str, Any]:
    return {
        "provider": payload.provider,
        "model_name": payload.model_name,
        "status": "ok",
        "latency_ms": 180,
        "message": "connection test passed",
    }


@app.post("/api/projects/{project_id}/export")
async def export_project(project_id: str) -> dict[str, Any]:
    timeline = TIMELINES.get(project_id)
    if not timeline:
        raise HTTPException(status_code=400, detail="timeline is empty")
    return {
        "project_id": project_id,
        "status": "success",
        "export_url": f"https://cdn.example.com/{project_id}/final-cut.mp4",
        "clip_count": len(timeline.get("clips", [])),
    }


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
