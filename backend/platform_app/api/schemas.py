"""Request schemas and small helpers shared by the API route modules."""

import json

from pydantic import BaseModel, Field

IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$"


class EnvironmentInput(BaseModel):
    id: str = Field(pattern=IDENTIFIER)
    product_id: str
    title: str = Field(min_length=1, max_length=120)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    database_name: str = "postgres"
    database_user: str = "postgres"
    deployment_config: str | None = None
    deployment_target: str | None = None
    product_test_settings: dict | None = None


class RegressionBindingInput(BaseModel):
    environment_id: str = Field(pattern=IDENTIFIER)


class LicenseKeyCreateInput(BaseModel):
    version: str = Field(pattern=r"^1\.[1-9][0-9]*$")
    password: str = Field(min_length=1, max_length=1024)


class LicenseKeyPasswordInput(BaseModel):
    old_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class LicenseKeyDeleteInput(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


def public_task(task: dict) -> dict:
    result = dict(task)
    result.pop("pending_events", None)
    result["parameters"] = json.loads(result["parameters"])
    result["cancel_requested"] = bool(result["cancel_requested"])
    return result
