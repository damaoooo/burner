from __future__ import annotations

from contextlib import suppress
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from burn_controller import BurnError, BurnOverlapError, MachineBurnRequest
from config import UI_ROOT
from slurm_controller import SlurmConflictError, SlurmController, SlurmError, compact_burn_job_dicts
from waveform_store import WaveformError, WaveformExistsError, WaveformStore


class WaveformCreate(BaseModel):
    name: str
    points: list[tuple[float, float]]


class SlurmSubmitPayload(BaseModel):
    nodes: int
    time_limit: str
    poll_ms: int = 10
    sample_ms: int = 200


class BurnMachinePayload(BaseModel):
    id: str
    enabled: bool = True
    burn_cpu: bool = True
    burn_gpu: bool = False
    delay_seconds: float = 0.0
    waveform_name: str


class BurnStartPayload(BaseModel):
    sync_mode: Literal["immediate", "delayed", "scheduled"] = "immediate"
    start_time_utc: str | None = None
    duration: str
    period: str
    tick_seconds: float = 0.1
    machines: list[BurnMachinePayload]


class BurnStartAllPayload(BaseModel):
    sync_mode: Literal["immediate", "scheduled"] = "immediate"
    start_time_utc: str | None = None
    duration: str
    period: str
    tick_seconds: float = 0.1
    waveform_name: str


class BurnStopPayload(BaseModel):
    machine_ids: list[str] | Literal["all"] | None = None
    job_ids: list[str] | Literal["all"] | None = None


class WebSocketHub:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    async def broadcast(self, payload: dict[str, object]) -> None:
        stale: list[WebSocket] = []
        for websocket in list(self._connections):
            try:
                await websocket.send_json(payload)
            except RuntimeError:
                stale.append(websocket)
        for websocket in stale:
            self._connections.discard(websocket)


waveforms = WaveformStore()
hub = WebSocketHub()


async def broadcast(payload: dict[str, object]) -> None:
    await hub.broadcast(payload)


slurm_controller = SlurmController(waveforms, broadcast)

app = FastAPI(title="Burner Shaheen SLURM WebUI")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/slurm/allocation")
async def allocation_status():
    try:
        return await slurm_controller.allocation_status()
    except SlurmError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/slurm/submit")
async def submit_allocation(payload: SlurmSubmitPayload):
    try:
        return await slurm_controller.submit_allocation(
            nodes=payload.nodes,
            time_limit=payload.time_limit,
            poll_ms=payload.poll_ms,
            sample_ms=payload.sample_ms,
        )
    except SlurmConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SlurmError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/slurm/release")
async def release_allocation():
    try:
        return await slurm_controller.release_allocation()
    except SlurmError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/slurm/load.csv")
async def download_load_csv():
    try:
        filename, content = slurm_controller.export_load_csv()
    except SlurmError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/slurm/load-series")
async def load_series(max_points: int = 1200, include_nodes: bool = False):
    try:
        return slurm_controller.load_series(max_points=max_points, include_nodes=include_nodes)
    except SlurmError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/machines")
async def list_machines(offset: int = 0, limit: int = 50):
    return await slurm_controller.list_machines(offset=offset, limit=limit)


@app.post("/api/machines/{machine_id}/connect")
async def connect_machine(machine_id: str):
    raise HTTPException(
        status_code=410,
        detail=f"SSH connect is disabled in Shaheen SLURM mode; node {machine_id} is managed by SLURM",
    )


@app.post("/api/machines/{machine_id}/disconnect")
async def disconnect_machine(machine_id: str):
    raise HTTPException(
        status_code=410,
        detail=f"SSH disconnect is disabled in Shaheen SLURM mode; use Release Allocation for node {machine_id}",
    )


@app.get("/api/machines/{machine_id}/hwinfo")
async def machine_hwinfo(machine_id: str):
    machine = await slurm_controller.get_machine(machine_id)
    if machine is not None:
        return machine["hw_info"]
    raise HTTPException(status_code=404, detail=f"unknown SLURM node: {machine_id}")


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
            delay_seconds=0.0,
            waveform_name=item.waveform_name,
        )
        for item in payload.machines
    ]
    try:
        jobs = await slurm_controller.start_burn(
            payload.sync_mode,
            payload.duration,
            payload.period,
            machines,
            payload.start_time_utc,
            payload.tick_seconds,
        )
    except BurnOverlapError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BurnError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return compact_burn_job_dicts([job.to_dict() for job in jobs])


@app.post("/api/burn/start-all")
async def start_burn_all(payload: BurnStartAllPayload):
    try:
        job = await slurm_controller.start_all_burn(
            payload.sync_mode,
            payload.duration,
            payload.period,
            payload.waveform_name,
            payload.start_time_utc,
            payload.tick_seconds,
        )
    except BurnOverlapError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BurnError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return [job.to_dict()]


@app.post("/api/burn/stop")
async def stop_burn(payload: BurnStopPayload):
    await slurm_controller.stop_burn(
        machine_ids=payload.machine_ids,
        job_ids=payload.job_ids,
    )
    return {"status": "stopped"}


@app.get("/api/burn/status")
async def burn_status():
    return slurm_controller.status()


@app.post("/api/update/{machine_id}")
async def update_machine(machine_id: str):
    raise HTTPException(
        status_code=410,
        detail=f"SSH update is disabled in Shaheen SLURM mode; worker startup rebuilds CPU backend for {machine_id}",
    )


@app.post("/api/sampling/apply")
async def apply_sampling_time():
    raise HTTPException(
        status_code=410,
        detail="Sampling rebuild is disabled in Shaheen SLURM mode; set worker polling before submitting allocation",
    )


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
        await hub.disconnect(websocket)


async def _send_snapshot(websocket: WebSocket) -> None:
    await websocket.send_json({"event": "allocation_changed", **(await slurm_controller.allocation_status())})
    for job in slurm_controller.status():
        await websocket.send_json(
            {
                "event": "burn_started",
                "job_id": job["job_id"],
                "id": job["machine_id"],
                "pid": job["pid"],
                "started_at": job["started_at"],
                "duration_seconds": job["duration_seconds"],
                "burn_cpu": job["burn_cpu"],
                "burn_gpu": job["burn_gpu"],
                "delay_seconds": job["delay_seconds"],
                "waveform_name": job["waveform_name"],
                "sync_mode": job["sync_mode"],
            }
        )


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
        await slurm_controller.stop_burn(job_ids="all")
