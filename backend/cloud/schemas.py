"""Pydantic request/response schemas for the cloud API."""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


# --- Auth ---
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: str
    email: EmailStr
    full_name: str | None
    is_admin: bool
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


# --- Plans / billing ---
class PlanResponse(BaseModel):
    slug: str
    name: str
    price_cents: int
    interval: str
    monthly_generation_limit: int
    monthly_character_limit: int
    max_voice_profiles: int
    features: list[str]

    class Config:
        from_attributes = True


class SubscriptionResponse(BaseModel):
    status: str
    plan: PlanResponse
    current_period_end: datetime | None
    cancel_at_period_end: bool

    class Config:
        from_attributes = True


class CheckoutRequest(BaseModel):
    plan_slug: str


class CheckoutResponse(BaseModel):
    url: str


# --- Usage ---
class UsageResponse(BaseModel):
    period: str
    generations_used: int
    generations_limit: int
    characters_used: int
    characters_limit: int


# --- API keys ---
class ApiKeyCreateRequest(BaseModel):
    name: str | None = None


class ApiKeyResponse(BaseModel):
    id: str
    name: str | None
    prefix: str
    last_used_at: datetime | None
    revoked: bool
    created_at: datetime

    class Config:
        from_attributes = True


class ApiKeyCreatedResponse(ApiKeyResponse):
    # Returned exactly once, includes the full secret.
    key: str


# --- Voice gateway ---
class SpeakRequest(BaseModel):
    text: str = Field(min_length=1)
    profile_id: str | None = None
    engine: str | None = None
    language: str | None = None


# --- Admin ---
class AdminUserRow(UserResponse):
    plan: str | None = None
    subscription_status: str | None = None
    generations_this_period: int = 0


class AdminStats(BaseModel):
    total_users: int
    active_subscriptions: int
    generations_this_period: int
    mrr_cents: int
