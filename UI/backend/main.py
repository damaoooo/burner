from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from burn_controller import BurnController, BurnError, BurnOverlapError, MachineBurnRequest
from config import ConfigError, ConfigStore, UI_ROOT
from file_transfer import FileTransfer
from machine_info import query_hw_info
from ssh_manager import SSHManager
from update_controller import UpdateConflictError, UpdateController
from waveform_store import WaveformError, WaveformExistsError, WaveformStore


class WaveformCreate(BaseModel):
    name: str
    points: list[tuple[float, float]]


class BurnMachinePayload(BaseModel):
    id: str
    enabled: bool = True
    burn_cpu: bool = True
    burn_gpu: bool = True
    delay_seconds: float = 0.0
    waveform_name: str


class BurnStartPayload(BaseModel):
    sync_mode: Literal["immediate", "delayed", "scheduled"] = "immediate"
    start_time_utc: str | None = None
    duration: str
    period: str
    machines: list[BurnMachinePayload]


class BurnStopPayload(BaseModel):
    machine_ids: list[str] | Literal["all"] | None = None
    job_ids: list[str] | Literal["all"] | None = None


class WebSocketHub:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self.active_ws_count = 0
        self._grace_task: asyncio.Task[None] | None = None

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
            self.active_ws_count = len(self._connections)
            if self._grace_task is not None:
                self._grace_task.cancel()
                self._grace_task = None

    async def disconnect(self, websocket: WebSocket) -> bool:
        async with self._lock:
            self._connections.discard(websocket)
            self.active_ws_count = len(self._connections)
            return self.active_ws_count == 0

    def schedule_grace_stop(self, callback) -> None:
        if self._grace_task is not None:
            self._grace_task.cancel()
        self._grace_task = asyncio.create_task(self._grace_stop(callback))

    async def broadcast(self, payload: dict[str, object]) -> None:
        async with self._lock:
            sockets = list(self._connections)
        stale: list[WebSocket] = []
        for websocket in sockets:
            try:
                await websocket.send_json(payload)
            except RuntimeError:
                stale.append(websocket)
        if stale:
            async with self._lock:
                for websocket in stale:
                    self._connections.discard(websocket)
                self.active_ws_count = len(self._connections)

    async def _grace_stop(self, callback) -> None:
        try:
            await asyncio.sleep(60)
            if self.active_ws_count == 0:
                await callback()
        except asyncio.CancelledError:
            return


config_store = ConfigStore()
waveforms = WaveformStore()
hub = WebSocketHub()
ssh_manager = SSHManager(config_store)
file_transfer = FileTransfer(ssh_manager)
hw_cache: dict[str, dict[str, object]] = {}


async def broadcast(payload: dict[str, object]) -> None:
    await hub.broadcast(payload)


burn_controller = BurnController(
    config_store,
    ssh_manager,
    file_transfer,
    waveforms,
    broadcast,
)
update_controller = UpdateController(
    config_store,
    ssh_manager,
    burn_controller,
    broadcast,
)


async def on_machine_status(machine_id: str, status: str, message: str | None) -> None:
    payload: dict[str, object] = {
        "event": "machine_status",
        "id": machine_id,
        "status": status,
    }
    if message:
        payload["message"] = message
    await broadcast(payload)


ssh_manager.set_status_callback(on_machine_status)

app = FastAPI(title="Burner WebUI")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/machines")
async def list_machines():
    try:
        machines = config_store.list_machines()
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [
        {
            **machine.to_dict(),
            "connection_status": ssh_manager.status_for(machine.id),
            "error_message": ssh_manager.error_for(machine.id),
            "hw_info": hw_cache.get(machine.id),
            "job": next(
                (
                    job.to_dict()
                    for job in burn_controller.job_registry.values()
                    if job.machine_id == machine.id
                ),
                None,
            ),
        }
        for machine in machines
    ]


@app.post("/api/machines/{machine_id}/connect")
async def connect_machine(machine_id: str, background_tasks: BackgroundTasks):
    _require_machine(machine_id)
    background_tasks.add_task(_connect_and_query, machine_id)
    return {"status": "connecting"}


@app.post("/api/machines/{machine_id}/disconnect")
async def disconnect_machine(machine_id: str):
    _require_machine(machine_id)
    await burn_controller.stop_burn(machine_ids=[machine_id])
    await ssh_manager.disconnect(machine_id)
    hw_cache.pop(machine_id, None)
    return {"status": "disconnected"}


@app.get("/api/machines/{machine_id}/hwinfo")
async def machine_hwinfo(machine_id: str):
    _require_machine(machine_id)
    try:
        info = await query_hw_info(machine_id, config_store, ssh_manager)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    hw_cache[machine_id] = info
    await broadcast({"event": "hw_info", "id": machine_id, **info})
    return info


@app.get("/api/waveforms")
async def list_waveforms():
    try:
        return [record.to_dict() for record in waveforms.list_waveforms()]
    except WaveformError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/waveforms/{name}")
async def get_waveform(name: str):
    try:
        return waveforms.get_waveform(name).to_dict()
    except WaveformError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/waveforms")
async def save_waveform(payload: WaveformCreate):
    try:
        return waveforms.save_waveform(payload.name, payload.points).to_dict()
    except WaveformExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WaveformError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/burn/start")
async def start_burn(payload: BurnStartPayload):
    machines = [
        MachineBurnRequest(
            id=item.id,
            enabled=item.enabled,
            burn_cpu=item.burn_cpu,
            burn_gpu=item.burn_gpu,
            delay_seconds=max(0.0, item.delay_seconds),
            waveform_name=item.waveform_name,
        )
        for item in payload.machines
    ]
    try:
        jobs = await burn_controller.start_burn(
            payload.sync_mode,
            payload.duration,
            payload.period,
            machines,
            payload.start_time_utc,
        )
    except BurnOverlapError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BurnError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return [job.to_dict() for job in jobs]


@app.post("/api/burn/stop")
async def stop_burn(payload: BurnStopPayload):
    await burn_controller.stop_burn(
        machine_ids=payload.machine_ids,
        job_ids=payload.job_ids,
    )
    return {"status": "stopped"}


@app.get("/api/burn/status")
async def burn_status():
    return burn_controller.status()


@app.post("/api/update/{machine_id}")
async def update_machine(machine_id: str, background_tasks: BackgroundTasks):
    _require_machine(machine_id)
    if burn_controller.has_jobs(machine_id):
        raise HTTPException(status_code=409, detail="Machine is currently burning")
    has_gpu = bool((hw_cache.get(machine_id) or {}).get("gpus"))
    background_tasks.add_task(_run_update_task, machine_id, has_gpu)
    return {"status": "started"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await hub.connect(websocket)
    try:
        await _send_snapshot(websocket)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        should_schedule_stop = await hub.disconnect(websocket)
        if should_schedule_stop:
            hub.schedule_grace_stop(lambda: burn_controller.stop_burn(job_ids="all"))


async def _connect_and_query(machine_id: str) -> None:
    try:
        await ssh_manager.connect(machine_id)
        info = await query_hw_info(machine_id, config_store, ssh_manager)
    except Exception:
        return
    hw_cache[machine_id] = info
    await broadcast({"event": "hw_info", "id": machine_id, **info})


async def _run_update_task(machine_id: str, has_gpu: bool) -> None:
    try:
        await update_controller.run_update(machine_id, has_gpu)
    except UpdateConflictError as exc:
        await broadcast(
            {
                "event": "update_done",
                "id": machine_id,
                "exit_code": 409,
                "message": str(exc),
            }
        )
    except Exception as exc:
        await broadcast(
            {
                "event": "update_log",
                "id": machine_id,
                "line": f"update failed: {exc}",
            }
        )
        await broadcast({"event": "update_done", "id": machine_id, "exit_code": 1})


async def _send_snapshot(websocket: WebSocket) -> None:
    for machine in config_store.list_machines():
        payload = {
            "event": "machine_status",
            "id": machine.id,
            "status": ssh_manager.status_for(machine.id),
        }
        error = ssh_manager.error_for(machine.id)
        if error:
            payload["message"] = error
        await websocket.send_json(payload)
        info = hw_cache.get(machine.id)
        if info is not None:
            await websocket.send_json({"event": "hw_info", "id": machine.id, **info})
    for job in burn_controller.job_registry.values():
        await websocket.send_json(
            {
                "event": "burn_started",
                "job_id": job.job_id,
                "id": job.machine_id,
                "pid": job.pid,
                "started_at": job.started_at,
                "duration_seconds": job.duration_seconds,
                "burn_cpu": job.burn_cpu,
                "burn_gpu": job.burn_gpu,
                "delay_seconds": job.delay_seconds,
                "waveform_name": job.waveform_name,
                "sync_mode": job.sync_mode,
            }
        )


def _require_machine(machine_id: str) -> None:
    try:
        config_store.get_machine(machine_id)
    except ConfigError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


FRONTEND_DIST = UI_ROOT / "frontend" / "dist"
FRONTEND_ASSETS = FRONTEND_DIST / "assets"

if FRONTEND_ASSETS.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_ASSETS), name="assets")


@app.get("/")
async def frontend_index():
    return _frontend_index()


@app.get("/{path:path}")
async def frontend_fallback(path: str):
    if path.startswith("api/") or path == "ws":
        raise HTTPException(status_code=404, detail="Not found")
    return _frontend_index()


def _frontend_index() -> FileResponse:
    index_path = FRONTEND_DIST / "index.html"
    if not index_path.exists():
        raise HTTPException(
            status_code=503,
            detail="Frontend build not found. Run 'npm run build' in UI/frontend first.",
        )
    return FileResponse(index_path)


@app.on_event("shutdown")
async def shutdown() -> None:
    with suppress(Exception):
        await burn_controller.stop_burn(job_ids="all")
    for machine in config_store.list_machines():
        with suppress(Exception):
            await ssh_manager.disconnect(machine.id)
