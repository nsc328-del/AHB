"""Loopback-only video editing workbench API."""
from __future__ import annotations

import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

import storage as db
import workflow
from decisions_api import router as decisions_router
from publication_api import router as publication_router


@asynccontextmanager
async def lifespan(application):
    db.initialize()
    db.mark_interrupted()
    import decisions
    decisions.reconcile_batches(startup=True)
    yield


app = FastAPI(title="Cutroom · Video Workbench", lifespan=lifespan)
app.include_router(decisions_router)
app.include_router(publication_router)
STATIC = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC, check_dir=False), name="static")


@app.middleware("http")
async def local_origin_guard(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin and request.method not in {"GET", "HEAD", "OPTIONS"}:
        parsed = urlparse(origin)
        host = request.headers.get("host", "")
        if parsed.netloc != host or parsed.scheme not in {"http", "https"}:
            return JSONResponse({"detail": "只接受当前本机工作台页面发起的操作"}, status_code=403)
    return await call_next(request)


@app.exception_handler(ValueError)
async def value_error_handler(request, error):
    return JSONResponse({"detail": str(error)[:600]}, status_code=400)


@app.exception_handler(KeyError)
async def missing_record_handler(request, error):
    return JSONResponse({"detail": "记录不存在"}, status_code=404)


@app.get("/")
def index():
    path = STATIC / "index.html"
    if not path.is_file():
        raise HTTPException(503, "页面文件尚未准备完成")
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "video-workbench", "storage": "sqlite", "ffmpeg": bool(shutil.which("ffmpeg")), "ffprobe": bool(shutil.which("ffprobe"))}


@app.get("/api/config")
def get_config():
    return workflow.providers().get_config()


@app.post("/api/config")
def save_config(payload: dict):
    try:
        return workflow.providers().save_config(payload)
    except workflow.providers().ProviderError as error:
        raise HTTPException(400, str(error)) from None


@app.get("/api/templates")
def templates():
    from skill_catalog import get_templates
    return {"templates": get_templates()}


@app.get("/api/skills")
def skills():
    from skill_catalog import get_skills
    return {"skills": get_skills()}


@app.post("/api/assets")
async def upload_assets(files: list[UploadFile] = File(...), purpose: str = Form("library")):
    if not files or len(files) > 30:
        raise HTTPException(400, "每次支持上传 1–30 份素材")
    purpose = workflow.upload_purpose(purpose)
    results, jobs = [], []
    for upload in files:
        name = Path(upload.filename or "未命名素材").name[:240]
        extension = Path(name).suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,8}", extension):
            extension = ".bin"
        target = db.DATA / "assets" / (db.identifier() + extension)
        size = 0
        registered = None
        try:
            with target.open("xb") as handle:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > 1024 * 1024 * 1024:
                        raise ValueError("单个素材不能超过 1GB")
                    handle.write(chunk)
            if size == 0:
                raise ValueError("不能上传空文件")
            registered = await run_in_threadpool(workflow.register_asset, target, name, purpose=purpose)
            queued = await run_in_threadpool(workflow.queue_uploaded_asset, registered["id"])
            jobs.extend(db.public_job(job) for job in queued)
            results.append(db.public_asset(db.get("assets", registered["id"])))
        except Exception:
            # Once registered, the original belongs to a durable asset record.
            # A queue problem must never leave that record pointing to a deleted file.
            if registered is None:
                target.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()
    return {"assets": results, "jobs": jobs, "job_ids": [job["id"] for job in jobs]}


@app.get("/api/assets")
def assets():
    return {"assets": [db.public_asset(asset) for asset in db.records("assets", 5000)]}


@app.get("/api/assets/{asset_id}")
def asset_detail(asset_id: str):
    asset = db.get("assets", asset_id)
    if not asset:
        raise HTTPException(404, "素材不存在")
    return db.public_asset(asset)


@app.post("/api/assets/{asset_id}/analyze")
def analyze_asset(asset_id: str):
    job = workflow.analyze(asset_id)
    return {"job_id": job["id"]}


@app.get("/api/transcription")
def transcription_status():
    import transcription
    return transcription.status()


@app.post("/api/assets/{asset_id}/transcribe")
def transcribe_asset(asset_id: str, payload: dict = {}):
    job = workflow.transcribe_asset(asset_id, payload.get("model"))
    return {"job_id": job["id"]}


@app.post("/api/references/{asset_id}/analyze")
def analyze_reference(asset_id: str):
    job = workflow.analyze(asset_id, reference=True)
    return {"job_id": job["id"]}


@app.post("/api/projects")
def create_project(payload: dict):
    job = workflow.create_project(payload)
    return {"job_id": job["id"], "project_id": job["payload"]["project_id"]}


@app.get("/api/projects")
def projects():
    return {"projects": [workflow.project_view(project["id"]) for project in db.records("projects", 5000)]}


@app.get("/api/projects/{project_id}")
def project_detail(project_id: str):
    return workflow.project_view(project_id)


@app.post("/api/projects/{project_id}/revise")
def revise_project(project_id: str, payload: dict):
    job = workflow.revise(project_id, payload.get("brief", ""), payload.get("base_plan_id"))
    return {"job_id": job["id"]}


@app.post("/api/projects/{project_id}/retry-plan")
def retry_initial_plan(project_id: str):
    job = workflow.retry_initial_plan(project_id)
    return {"job_id": job["id"], "project_id": project_id}


@app.put("/api/projects/{project_id}/plan")
def update_plan(project_id: str, payload: dict):
    return {"plan": workflow.save_plan(project_id, payload)}


@app.post("/api/projects/{project_id}/render")
def render_project(project_id: str, payload: dict = {}):
    job = workflow.render(project_id, payload.get("plan_id"))
    return {"job_id": job["id"]}


@app.post("/api/generations")
def generate_video(payload: dict):
    job = workflow.generate(payload)
    return {"job_id": job["id"]}


@app.post("/api/generation-plans")
def generation_plan(payload: dict):
    job = workflow.generation_plan(payload)
    return {"job_id": job["id"]}


@app.get("/api/jobs")
def jobs():
    return {"jobs": [db.public_job(job) for job in db.records("jobs")]}


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str):
    job = db.get("jobs", job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return db.public_job(job)


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str):
    job = workflow.resume_generation(job_id)
    return {"job_id": job["id"]}


@app.get("/api/media/{relative:path}")
def serve_media(relative: str):
    try:
        path = db.safe_media_path(relative)
    except FileNotFoundError:
        raise HTTPException(404, "文件不存在") from None
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8787, reload=False)
