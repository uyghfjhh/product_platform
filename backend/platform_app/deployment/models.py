from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NodeOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]*$", max_length=80)
    port: int = Field(ge=1024, le=65535)
    data_dir: str

    @field_validator("data_dir")
    @classmethod
    def absolute_path(cls, value):
        if (
            not value.startswith("/")
            or ".." in PurePosixPath(value).parts
            or value == "/"
        ):
            raise ValueError("需要非根绝对路径，不能包含 ..")
        return value.rstrip("/")


class DeploymentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    product_id: str
    template_id: str
    mode: Literal["new", "adopt", "import"] = "new"
    host: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.:-]*$", max_length=253)
    home: str = ""
    data_root: str = ""
    license_file: str = ""
    base_port: int = Field(default=15432, ge=1024, le=65535)
    nodes: list[NodeOverride] = Field(default_factory=list, max_length=64)
    parameters: dict = Field(default_factory=dict)
    source_yaml: str = Field(default="", max_length=262144)
    target: str = ""

    @field_validator("home", "data_root", "license_file")
    @classmethod
    def optional_absolute_path(cls, value):
        return NodeOverride.absolute_path(value) if value else value


class DraftInput(BaseModel):
    spec: DeploymentSpec
    expected_revision: int = Field(default=0, ge=0)


class DiscoveryInput(BaseModel):
    host: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.:-]*$", max_length=253)
    home: str = ""
    data_dir: str = ""

    @field_validator("home", "data_dir")
    @classmethod
    def optional_path(cls, value):
        return NodeOverride.absolute_path(value) if value else value
