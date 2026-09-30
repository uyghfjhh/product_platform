from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NodeOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]*$", max_length=80)
    host: str = Field(default="", pattern=r"^$|^[a-zA-Z][a-zA-Z0-9_]*$", max_length=63)
    port: int = Field(ge=1024, le=65535)
    data_dir: str
    role: Literal["primary", "standby"] = "standby"

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


class HostResource(BaseModel):
    """Explicit host + SSH credential resource for multi-host deployments."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]*$", max_length=63)
    address: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.:-]*$", max_length=253)
    ssh_user: str = Field(default="", pattern=r"^$|^[a-zA-Z_][a-zA-Z0-9_.-]*$", max_length=64)
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    ssh_identity_file: str = ""
    ssh_connect_timeout: int = Field(default=10, ge=1, le=120)
    home: str = ""  # installation home override on this host

    @field_validator("ssh_identity_file", "home")
    @classmethod
    def optional_absolute_path(cls, value):
        return NodeOverride.absolute_path(value) if value else value

    def ssh_options(self):
        options = {}
        if self.ssh_user:
            options["user"] = self.ssh_user
        if self.ssh_port:
            options["port"] = self.ssh_port
        if self.ssh_identity_file:
            options["identity_file"] = self.ssh_identity_file
        if self.ssh_connect_timeout != 10:
            options["connect_timeout"] = self.ssh_connect_timeout
        return options or None


class DeploymentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    product_id: str
    template_id: str
    mode: Literal["new", "adopt", "import", "free"] = "new"
    cluster_name: str = Field(default="cluster", pattern=r"^[a-zA-Z][a-zA-Z0-9_]*$", max_length=63)
    host: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.:-]*$", max_length=253)
    hosts: list[HostResource] = Field(default_factory=list, max_length=16)
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
    ssh: dict = Field(default_factory=dict)

    @field_validator("home", "data_dir")
    @classmethod
    def optional_path(cls, value):
        return NodeOverride.absolute_path(value) if value else value

    @field_validator("ssh")
    @classmethod
    def ssh_options_shape(cls, value):
        allowed = {"user", "port", "identity_file", "connect_timeout"}
        if set(value) - allowed:
            raise ValueError("SSH 选项含未知字段")
        return value
