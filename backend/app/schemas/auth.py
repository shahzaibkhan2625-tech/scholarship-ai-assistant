import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, field_validator

MIN_PASSWORD_CHARS = 8
MAX_PASSWORD_BYTES = 72  # bcrypt reads at most 72 bytes; longer input is refused, never cut


def _lower_email(value: object) -> object:
    return value.strip().lower() if isinstance(value, str) else value


class SignupRequest(BaseModel):
    email: EmailStr
    password: str

    normalise_email = field_validator("email", mode="before")(_lower_email)

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        if len(value) < MIN_PASSWORD_CHARS:
            raise ValueError(f"Password must be at least {MIN_PASSWORD_CHARS} characters")
        if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str  # no length rule: accounts created before the rules must keep signing in

    normalise_email = field_validator("email", mode="before")(_lower_email)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    created_at: datetime

    model_config = {"from_attributes": True}
