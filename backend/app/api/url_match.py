from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.data.repositories.db import get_db
from app.models.user import User
from app.schemas.match import FetchFailure, UrlMatchResult
from app.services.profile import get_or_create_profile
from app.workflows.url_match.graph import run_url_match

router = APIRouter()


class MatchUrlRequest(BaseModel):
    url: HttpUrl


@router.post("/match-url", response_model=UrlMatchResult)
def match_url(
    payload: MatchUrlRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = get_or_create_profile(db, current_user.id)
    result = run_url_match(db, current_user.id, profile, str(payload.url))

    if isinstance(result, FetchFailure):
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=result.model_dump())

    return result
