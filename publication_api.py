"""HTTP surface for local publication history; no publishing capability."""
from fastapi import APIRouter, HTTPException, Query
import publication_data as data

router = APIRouter()


@router.get("/api/workspaces")
def workspaces():
    return data.business_workspaces()


@router.post("/api/workspaces")
def create_workspace(payload: dict):
    return data.create_workspace(payload.get("name"))


@router.get("/api/workspaces/{workspace_id}/history")
def workspace_history(workspace_id: str, platform: str = "", account: str = "", query: str = "",
                      limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0)):
    return data.list_history(platform, account, query, limit, offset, workspace_id=workspace_id)


@router.get("/api/history/status")
def history_status():
    return data.history_status()


@router.post("/api/history/sync")
def history_sync():
    return data.sync_history()


@router.get("/api/history")
def history(platform: str = "", account: str = "", query: str = "", limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0)):
    return data.list_history(platform, account, query, limit, offset)


@router.get("/api/history/dashboard")
def history_dashboard(workspace_id: str = "xingshang", platform: str = "douyin", account: str = "",
                      date_from: str = "", date_to: str = ""):
    try:
        return data.history_dashboard(workspace_id, platform, account, date_from, date_to)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except KeyError:
        raise HTTPException(404, "项目不存在") from None


@router.get("/api/history/{publication_id}")
def history_detail(publication_id: str):
    return data.history_detail(publication_id)


@router.get("/api/projects/{project_id}/publications")
def publications(project_id: str):
    return data.project_publications(project_id)


@router.post("/api/projects/{project_id}/publications")
def bind_publication(project_id: str, payload: dict):
    try:
        return data.bind_publication(project_id, payload.get("publication_id"), payload.get("render_id"), payload.get("confirmed"))
    except data.BindingConflict as exc:
        raise HTTPException(409, str(exc)) from None


@router.delete("/api/projects/{project_id}/publications/{binding_id}")
def unbind_publication(project_id: str, binding_id: str):
    return data.unbind_publication(project_id, binding_id)
