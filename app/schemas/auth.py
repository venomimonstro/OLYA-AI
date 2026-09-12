import re
from pydantic import BaseModel, Field, field_validator


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _email(value: str) -> str:
    value = value.strip().lower()
    if not _EMAIL_RE.match(value):
        raise ValueError("invalid email")
    return value


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=10, max_length=256)
    display_name: str = Field(default="", max_length=120)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _email(value)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _email(value)


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _email(value)


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)


class AuthResponse(BaseModel):
    access_token: str | None = None
    token_type: str = "bearer"
    user_id: str
    verification_required: bool = False
    is_admin: bool = False


class MeResponse(BaseModel):
    id: str
    email: str
    display_name: str
    is_admin: bool = False
    email_verified: bool | None = None
