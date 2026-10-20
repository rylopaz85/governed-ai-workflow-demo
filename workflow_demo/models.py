"""Validated, deliberately limited inputs and classification contracts."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Department = Literal["sales", "support", "operations"]
Urgency = Literal["normal", "high"]


class LeadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    lead_ref: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    source: Literal["website", "partner", "email"]
    message: str = Field(min_length=5, max_length=2000)


class Classification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    department: Department
    urgency: Urgency
    confidence: float = Field(ge=0.0, le=1.0)


class IntakeResponse(BaseModel):
    workflow_id: str
    status: str
    department: Department
    urgency: Urgency
    requires_approval: bool
    replayed: bool
