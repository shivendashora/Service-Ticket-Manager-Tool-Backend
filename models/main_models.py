from typing import Literal

from pydantic import BaseModel, EmailStr, Field


# ---------------------------------------------------------
# SCHEMAS
# ---------------------------------------------------------

class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AssignTicketsRequest(BaseModel):
    ticket_id: list[int]
    to_user_id: int


TicketStatus = Literal["open", "in_progress", "resolved", "closed"]

UserRole = Literal["user", "admin"]


class UpdateTicketRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    description: str | None = Field(default=None, min_length=1)
    estimated_time: int | None = Field(default=None, ge=0)
    status: TicketStatus | None = None


class UpdateUserRoleRequest(BaseModel):
    role: UserRole



    

