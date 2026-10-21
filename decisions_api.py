"""Candidate and editorial-decision HTTP routes."""
from fastapi import APIRouter

import decisions

router = APIRouter()


@router.post("/api/projects/{project_id}/candidates")
def create_candidates(project_id: str, payload: dict):
    return decisions.create_candidates(project_id, payload)


@router.get("/api/candidate-batches/{batch_id}")
def candidate_batch(batch_id: str):
    return decisions.batch_view(batch_id)


@router.post("/api/candidate-batches/{batch_id}/events")
def candidate_event(batch_id: str, payload: dict):
    return decisions.record_event(batch_id, payload)


@router.post("/api/candidate-batches/{batch_id}/retry-previews")
def retry_previews(batch_id: str, payload: dict = {}):
    return decisions.retry_previews(batch_id, payload)


@router.get("/api/projects/{project_id}/decisions")
def project_decisions(project_id: str):
    return decisions.project_decisions(project_id)
