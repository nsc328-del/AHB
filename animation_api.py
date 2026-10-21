"""HTTP routes for versioned animation work."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

import animation_workflow as animations
import storage as db

router = APIRouter()


@router.get("/api/animations")
def animation_list():
    return {"animations": animations.list_animations()}


@router.post("/api/animations")
def animation_create(payload: dict):
    return animations.create(payload)


@router.get("/api/animations/{animation_id}")
def animation_detail(animation_id: str, revision_id: str | None = None):
    return animations.get_animation(animation_id, revision_id)


@router.get("/api/animations/{animation_id}/source")
def animation_source(animation_id: str, revision_id: str | None = None):
    view = animations.get_animation(animation_id, revision_id)
    public = view.get("revision")
    if not public:
        raise ValueError("动画方案尚未生成")
    revision = db.get("animation_revisions", public["id"])
    scenes = {s["id"]: s for s in revision["scenes"]}
    for row in public["scenes"]:
        source = animations._source(scenes[row["id"]].get("source_path"))
        if source is None:
            raise ValueError("部分片段尚未生成，请完成后再下载工程")
        row["source_html"] = source
    return JSONResponse({"format": "animation-project", "schema_version": 1, "revision": public}, headers={
        "Content-Disposition": f'attachment; filename="animation-{revision["id"]}.json"',
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@router.patch("/api/animations/{animation_id}/revisions/{revision_id}")
def animation_edit(animation_id: str, revision_id: str, payload: dict):
    return animations.edit(animation_id, revision_id, payload)


@router.post("/api/animations/{animation_id}/render")
def animation_render(animation_id: str, payload: dict):
    return animations.render(animation_id, payload)


@router.post("/api/animations/{animation_id}/revise")
def animation_revise(animation_id: str, payload: dict):
    return animations.revise(animation_id, payload)


@router.post("/api/animations/{animation_id}/retry")
def animation_retry(animation_id: str, payload: dict):
    return animations.retry(animation_id, payload)
