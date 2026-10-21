"""Read-only usage receipts; querying never calls a provider."""
from fastapi import APIRouter, Query

import usage

router = APIRouter()


@router.get("/api/usage")
def get_usage(scope: str = "global", project_id: str | None = None,
              asset_id: str | None = None, job_id: str | None = None,
              batch_id: str | None = None, animation_id: str | None = None,
              limit: int = Query(50, ge=1, le=100)):
    return usage.summary(scope, project_id=project_id, asset_id=asset_id,
                         job_id=job_id, batch_id=batch_id, animation_id=animation_id, limit=limit)
