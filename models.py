from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class CheckStatus(str, Enum):
    PASSED = "Passed"
    WARNING = "Warning"
    FAILED = "Failed"


@dataclass
class SystemCheck:
    id: str
    name: str
    status: CheckStatus
    details: str
    suggested_fix: str = ""
    verify_command: str = ""


@dataclass
class CommandLogEntry:
    step_name: str
    command: str
    working_directory: str
    started_at: datetime
    ended_at: datetime | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    success: bool = False
    user_message: str = ""
    technical_error: str = ""
    suggested_fix: str = ""

    def duration_seconds(self) -> float | None:
        if self.ended_at is None:
            return None
        return (self.ended_at - self.started_at).total_seconds()


@dataclass
class ConfigValues:
    ip_address: str
    local_ae_title: str
    client_id: str


@dataclass
class GeneratedFiles:
    somatiq_dir: str
    env_path: str
    compose_path: str
    env_backups: list[str] = field(default_factory=list)
    compose_backups: list[str] = field(default_factory=list)


@dataclass
class ServiceStatus:
    name: str
    state: str
    health: str = ""
    running: bool = False


@dataclass
class PostDeployVerification:
    ip_url: str
    ip_reachable: bool
    localhost_url: str
    localhost_reachable: bool
    port_11112_listening: bool
    messages: list[str] = field(default_factory=list)


@dataclass
class DeployStep:
    id: str
    label: str
    status: str = "pending"  # pending | running | done | failed
    fix: str = ""


@dataclass
class DeploymentState:
    running: bool = False
    success: bool | None = None
    last_error: str = ""
    failed_step_label: str = ""
    services: list[ServiceStatus] = field(default_factory=list)
    compose_ps_output: str = ""
    post_deploy: PostDeployVerification | None = None
    steps: list[DeployStep] = field(default_factory=list)


@dataclass
class DebugReportData:
    timestamp: str
    system_checks: list[SystemCheck]
    config: dict[str, str]
    file_paths: dict[str, object]
    compose_ps: str = ""
    service_log_summary: str = ""
    failed_checks: list[str] = field(default_factory=list)
    suggested_fixes: list[str] = field(default_factory=list)
    terminal_text: str = ""
    deployment_error: str = ""