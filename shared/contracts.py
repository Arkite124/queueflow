from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Status(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL = {Status.SUCCEEDED, Status.FAILED, Status.CANCELLED}
TRANSITIONS = {
    Status.QUEUED: {Status.RUNNING, Status.CANCELLED},
    Status.RUNNING: {Status.SUCCEEDED, Status.RETRY_WAIT, Status.FAILED, Status.CANCELLED},
    Status.RETRY_WAIT: {Status.QUEUED, Status.FAILED, Status.CANCELLED},
}


def transition(job, target: Status):
    if target not in TRANSITIONS.get(job.status, set()):
        raise ValueError(f"Invalid transition: {job.status} -> {target}")
    job.status = target


class ProcessingOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required_columns: list[str] = Field(default_factory=list, max_length=100)
    required_values: list[str] = Field(default_factory=list, max_length=100)
    trim_whitespace: bool = True
    remove_empty_rows: bool = True
    normalize_columns: bool = True
    duplicate_columns: list[str] = Field(default_factory=list, max_length=100)
    remove_duplicates: bool = True
    invalid_rows: Literal["exclude", "keep"] = "exclude"
    safe_export: bool = True
    column_types: dict[str, Literal["integer", "float", "date", "email"]] = Field(default_factory=dict)

    @field_validator("required_columns", "required_values", "duplicate_columns")
    @classmethod
    def valid_columns(cls, value):
        if any(not x or len(x) > 128 for x in value) or len(set(value)) != len(value):
            raise ValueError("Column names must be unique and 1–128 characters")
        return value

    @field_validator("column_types")
    @classmethod
    def valid_types(cls, value):
        if len(value) > 100 or any(not x or len(x) > 128 for x in value):
            raise ValueError("At most 100 named type rules are allowed")
        return value
