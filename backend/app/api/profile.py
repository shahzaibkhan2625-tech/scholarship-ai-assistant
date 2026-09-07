import io
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.data.repositories.db import get_db
from app.models.user import User
from app.schemas.profile import Profile as ProfileSchema
from app.schemas.profile import ProfileCriterion as ProfileCriterionSchema
from app.schemas.profile import ProfileCriterionRequest, ProfileUpdateRequest
from app.services.profile import InvalidCriterionKindError, get_or_create_profile, update_profile, upsert_criterion
from app.tools.extract_profile import extract_profile_from_text

router = APIRouter()


def _extract_text_from_upload(file: UploadFile, raw_bytes: bytes) -> str:
    if (file.content_type or "").endswith("pdf") or (file.filename or "").lower().endswith(".pdf"):
        reader = PdfReader(io.BytesIO(raw_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return raw_bytes.decode("utf-8", errors="ignore")


@router.get("", response_model=ProfileSchema)
def get_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileSchema:
    profile = get_or_create_profile(db, current_user.id)
    return ProfileSchema.model_validate(profile)


@router.put("", response_model=ProfileSchema)
def put_profile(
    payload: ProfileUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileSchema:
    profile = update_profile(
        db,
        current_user.id,
        payload.to_update_dict(),
        education_records=[r.model_dump() for r in payload.education_records] if payload.education_records is not None else None,
        test_scores=[r.model_dump() for r in payload.test_scores] if payload.test_scores is not None else None,
        experience=[r.model_dump() for r in payload.experience] if payload.experience is not None else None,
    )
    return ProfileSchema.model_validate(profile)


@router.post("/criteria", response_model=list[ProfileCriterionSchema])
def post_criteria(
    payload: ProfileCriterionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ProfileCriterionSchema]:
    try:
        criteria = upsert_criterion(
            db,
            current_user.id,
            criterion_id=payload.id,
            dimension=payload.dimension,
            operator=payload.operator,
            value=payload.value,
            kind=payload.kind,
            weight=payload.weight,
            note=payload.note,
        )
    except InvalidCriterionKindError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    return [ProfileCriterionSchema.model_validate(c) for c in criteria]


@router.post("/import-cv", response_model=ProfileUpdateRequest)
async def import_cv(
    file: UploadFile,
    current_user: User = Depends(get_current_user),
) -> ProfileUpdateRequest:
    raw_bytes = await file.read()
    text = _extract_text_from_upload(file, raw_bytes)
    # Suggestions only — never auto-saved; the user must confirm via PUT /profile.
    return extract_profile_from_text(text)
