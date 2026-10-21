"""Local operations calendar API."""
from fastapi import APIRouter
import operations

router = APIRouter(prefix="/api/operations")


@router.get("")
def state():
    return operations.view()


@router.put("/context")
def context(payload: dict):
    return operations.save_context(payload)


@router.post("/hotspots/refresh")
def refresh():
    return operations.refresh_hotspots()


@router.post("/generate")
def generate(payload: dict):
    return operations.generate(payload)


@router.post("/ideas")
def create(payload: dict):
    return operations.create_idea(payload)


@router.patch("/ideas/{idea_id}")
def edit(idea_id: str, payload: dict):
    return operations.change_idea(idea_id, payload)


@router.delete("/ideas/{idea_id}")
def remove(idea_id: str, expected_revision: int):
    return operations.change_idea(idea_id, {"expected_revision": expected_revision}, "delete")


@router.post("/ideas/{idea_id}/restore")
def restore(idea_id: str, payload: dict):
    return operations.change_idea(idea_id, payload, "restore")
