"""Authoritative response contracts used by HTTP and generated frontend types."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .schemas import EnvironmentInput


class ResponseModel(BaseModel):
    model_config = ConfigDict(extra='allow')


class TestProfile(BaseModel):
    id: str
    title: str
    suites: list[str]
    deployment_targets: list[str]


class Product(ResponseModel):
    id: str
    title: str
    description: str
    capabilities: list[str]
    source_path: str
    test_profiles: list[TestProfile] = Field(default_factory=list)


class Environment(EnvironmentInput):
    model_config = ConfigDict(extra='allow')
    created_at: str
    desired_deployment_plan_id: str | None = None
    applied_deployment_plan_id: str | None = None
    applied_deployment_config: str | None = None
    deployment_status: str | None = None


class TaskProgress(ResponseModel):
    done: int
    total: int
    label: str


class Task(ResponseModel):
    id: str
    environment_id: str
    action: str
    target: str
    status: Literal['QUEUED', 'RUNNING', 'CANCELLING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'RECOVERY_REQUIRED']
    reason: str | None = None
    parameters: dict[str, Any]
    submission_key: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    process_id: int | None = None
    cancel_requested: bool
    progress: TaskProgress | None = None
    last_sequence: int
    dispatch_pending: bool = False
    dispatch_error: str | None = None
    last_dispatched_at: str | None = None


class Event(BaseModel):
    task_id: str
    sequence: int
    recorded_at: str
    event_type: str
    payload: dict[str, Any]


class Case(ResponseModel):
    suite: str
    suite_title: str | None = None
    suite_description: str | None = None
    target: str
    name: str | None = None
    title: str
    core_id: str | None = None
    summary: str | None = None
    enabled: bool
    tags: list[str]


class Result(ResponseModel):
    product_id: str
    environment_id: str
    target: str
    profile: str
    status: str
    reason: str | None = None
    artifact_dir: str | None = None
    updated_at: str


class RegressionBinding(BaseModel):
    product_id: str
    profile_id: str
    environment_id: str
    updated_at: str


class Action(BaseModel):
    id: str
    title: str
    capability: str
    changes_environment: bool
    parameter_schema: dict[str, Any] = Field(default_factory=dict)
