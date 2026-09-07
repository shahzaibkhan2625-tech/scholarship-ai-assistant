import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.agents.research_qa.agent import answer_question
from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.qa import QaAnswer

router = APIRouter()


class QaRequest(BaseModel):
    question: str


@router.post("/{scholarship_id}/qa", response_model=QaAnswer)
def ask_question(
    scholarship_id: uuid.UUID,
    payload: QaRequest,
    current_user: User = Depends(get_current_user),
) -> QaAnswer:
    return answer_question(payload.question, str(scholarship_id))
