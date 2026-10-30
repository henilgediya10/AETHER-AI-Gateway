from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class _EmailMixin(BaseModel):
    """Shared by register and login so both treat emails the same way."""

    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def clean_email(cls, value):
        if isinstance(value, str):
            value = value.strip().lower()
            if "<" in value or ">" in value:
                raise ValueError("Invalid email address")
        return value


# 1. Customer registration
class UserRegister(_EmailMixin):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=8, max_length=128, repr=False)

    @field_validator("password")
    @classmethod
    def password_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Password cannot be only spaces")
        return value


# 2. Customer information returned after registration
class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    is_active: bool


# 3. Customer login
class UserLogin(_EmailMixin):
    model_config = ConfigDict(extra="forbid")

    # No min_length: login must not fail for an older, shorter password.
    # max_length matches registration, so nobody can send a huge password to burn CPU.
    password: str = Field(min_length=1, max_length=128, repr=False)


# 4. JWT token response
class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


# Name used by routes/auth.py in aether_backend_auth_final.zip
Token = TokenResponse