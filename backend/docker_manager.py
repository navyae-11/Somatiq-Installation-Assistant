from __future__ import annotations

import json
import socket
import subprocess
import threading
import webbrowser
from pathlib import Path
from typing import Callable

import requests

from backend.config_manager import REQUIRED_SERVICES, SOMATIQ_DIR
from backend.debug_logger import DebugLogger
from backend.diagnostics import diagnose_failure
from backend.models import DeployStep, DeploymentState, PostDeployVerification, ServiceStatus
from backend.system_checker import find_chrome_path

EXPECTED_SERVICES = REQUIRED_SERVICES
PULL_TIMEOUT = 3600
UP_TIMEOUT = 600

DEPLOY_STEP_DEFS: list[tuple[str, str, list[str], int | None, str]] = [
    (
        "validate",
        "Validating docker-compose.yml",
        ["docker", "compose", "config"],
        120,
        "Fix docker-compose.yml in Documents\\Somatiq.",
    ),
    (
        "pull",
        "Pulling Docker images",
        ["docker", "compose", "pull"],
        PULL_TIMEOUT,
        "Check internet connection or Docker login.",
    ),
    (
        "start",
        "Starting PACS containers",
        ["docker", "compose", "up", "-d"],
        UP_TIMEOUT,
        "Check Docker is running and ports are free.",
    ),
    (
        "status",
        "Checking PACS status",
        ["docker", "compose", "ps"],
        60,
        "Wait 1–2 minutes, then check docker compose logs pacs_ui.",
    ),
]

STEP_FAILURE_FIX: dict[str, str] = {
    "pull": "Check internet connection or Docker login.",
    "validate": "Open Documents\\Somatiq and verify docker-compose.yml.",
    "start": "Open Docker Desktop and ensure required ports are free.",
    "status": "Run deployment again after containers finish starting.",
}


class DockerManager:
    def __init__(self, logger: DebugLogger) -> None:
        self.logger = logger
        self._stop_event = threading.Event()
        self.state = DeploymentState()

    def stop_requested(self) -> None:
        self._stop_event.set()

    def reset_stop(self) -> None:
        self._stop_event.clear()

    def _init_deploy_steps(self) -> list[DeployStep]:
        return [DeployStep(id=sid, label=label) for sid, label, _, _, _ in DEPLOY_STEP_DEFS]

    def _set_step(self, step_id: str, status: str, fix: str = "") -> None:
        for step in self.state.steps:
            if step.id == step_id:
                step.status = status
                if fix:
                    step.fix = fix
                return

    def deploy(self, ip: str, on_complete: Callable[[bool], None] | None = None) -> None:
        self.state = DeploymentState(running=True, success=None, steps=self._init_deploy_steps())
        self.reset_stop()

        try:
            for step_id, label, command, timeout, default_fix in DEPLOY_STEP_DEFS:
                if self._stop_event.is_set():
                    self.logger.run(
                        "Deployment stopped by user",
                        ["docker", "compose", "down"],
                        SOMATIQ_DIR,
                        stream=True,
                        timeout=180,
                    )
                    self.state.running = False
                    self.state.success = False
                    self.state.last_error = "Deployment stopped by user."
                    if on_complete:
                        on_complete(False)
                    return

                self._set_step(step_id, "running")
                entry = self.logger.run(label, command, SOMATIQ_DIR, stream=True, timeout=timeout)
                if not entry.success:
                    diagnosis = diagnose_failure(entry)
                    fix = diagnosis["how_to_fix"] or STEP_FAILURE_FIX.get(step_id, default_fix)
                    entry.user_message = diagnosis["what_failed"]
                    entry.suggested_fix = fix
                    self._set_step(step_id, "failed", fix)
                    self.state.failed_step_label = label
                    self.state.last_error = f"Deployment failed at: {label}"
                    self.state.running = False
                    self.state.success = False
                    if on_complete:
                        on_complete(False)
                    return
                self._set_step(step_id, "done")

            self.state.compose_ps_output = self.logger.entries[-1].stdout if self.logger.entries else ""
            self.state.services = self._parse_compose_ps(self.state.compose_ps_output)

            for service_name in EXPECTED_SERVICES:
                service = self._find_service(service_name)
                if service is None or not service.running:
                    self.logger.run(
                        f"Fetch logs: {service_name}",
                        ["docker", "compose", "logs", service_name, "--tail=100"],
                        SOMATIQ_DIR,
                        stream=False,
                        timeout=120,
                    )
                    if service is None or not service.running:
                        fix = f"Check logs for {service_name} in technical logs."
                        self._set_step("status", "failed", fix)
                        self.state.failed_step_label = "Checking PACS status"
                        self.state.last_error = f"PACS service '{service_name}' is not running."
                        self.state.running = False
                        self.state.success = False
                        if on_complete:
                            on_complete(False)
                        return

            self.state.post_deploy = self._verify_post_deploy(ip)
            self.logger.run(
                "Post-deploy verification",
                ["echo", "HTTP and port checks"],
                SOMATIQ_DIR,
                stream=False,
                timeout=5,
                user_message="\n".join(self.state.post_deploy.messages),
            )

            all_running = all(
                self._find_service(name) and self._find_service(name).running
                for name in EXPECTED_SERVICES
            )
            ui_ok = (
                self.state.post_deploy.ip_reachable
                or self.state.post_deploy.localhost_reachable
            )

            if all_running and ui_ok:
                self.state.success = True
                self.state.last_error = ""
                self.open_chrome(ip)
                self.logger.run(
                    "Open PACS in Chrome",
                    ["chrome", f"http://{ip}:4000"],
                    SOMATIQ_DIR,
                    stream=False,
                    timeout=5,
                    user_message=f"Opened http://{ip}:4000",
                )
            else:
                self.state.success = False
                fix = "Wait 1–2 minutes and open http://<server-ip>:4000 in Chrome."
                self._set_step("status", "failed", fix)
                if not self.state.last_error:
                    self.state.last_error = "PACS UI is not reachable yet."
                    self.state.failed_step_label = "Checking PACS status"

            self.state.running = False
            if on_complete:
                on_complete(bool(self.state.success))
        except Exception as exc:
            self.state.running = False
            self.state.success = False
            self.state.last_error = str(exc)
            self.logger.run(
                "Deployment exception",
                ["echo", "exception"],
                SOMATIQ_DIR,
                stream=False,
                user_message=str(exc),
                technical_error=str(exc),
            )
            if on_complete:
                on_complete(False)

    def stop(self) -> None:
        self.stop_requested()
        self.logger.run(
            "Stop containers",
            ["docker", "compose", "down"],
            SOMATIQ_DIR,
            stream=True,
            timeout=180,
        )
        self.state.running = False

    def restart(self) -> None:
        self.logger.run(
            "Restart containers",
            ["docker", "compose", "restart"],
            SOMATIQ_DIR,
            stream=True,
            timeout=600,
        )
        ps_entry = self.logger.run(
            "Service status after restart",
            ["docker", "compose", "ps"],
            SOMATIQ_DIR,
            stream=False,
            timeout=60,
        )
        self.state.compose_ps_output = ps_entry.stdout
        self.state.services = self._parse_compose_ps(ps_entry.stdout)

    def _find_service(self, name: str) -> ServiceStatus | None:
        for service in self.state.services:
            if service.name == name or service.name.startswith(name):
                return service
        return None

    def _parse_compose_ps(self, output: str) -> list[ServiceStatus]:
        """Parse docker compose ps; prefer JSON format when available."""
        json_result = self._parse_ps_json()
        if json_result:
            return json_result
        return self._parse_ps_table(output)

    def _parse_ps_json(self) -> list[ServiceStatus]:
        try:
            result = subprocess.run(
                ["docker", "compose", "ps", "--format", "json"],
                cwd=SOMATIQ_DIR,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return []
            services: list[ServiceStatus] = []
            for line in result.stdout.strip().splitlines():
                if not line.strip():
                    continue
                data = json.loads(line)
                name = data.get("Service") or data.get("Name", "")
                state = data.get("State", "")
                health = data.get("Health", "")
                running = state.lower() in ("running", "healthy") or "up" in state.lower()
                services.append(
                    ServiceStatus(
                        name=str(name).split("-")[0] if "-" in str(name) else str(name),
                        state=state,
                        health=health,
                        running=running,
                    )
                )
            return services
        except Exception:
            return []

    def _parse_ps_table(self, output: str) -> list[ServiceStatus]:
        services: list[ServiceStatus] = []
        lines = [line for line in output.splitlines() if line.strip()]
        if len(lines) < 2:
            return services
        for line in lines[1:]:
            parts = line.split()
            if len(parts) < 2:
                continue
            raw_name = parts[0]
            base_name = raw_name.rsplit("-", 1)[0] if "-" in raw_name else raw_name
            for expected in EXPECTED_SERVICES:
                if expected in raw_name or raw_name.startswith(expected):
                    base_name = expected
                    break
            state = " ".join(parts[2:]) if len(parts) > 2 else ""
            running = any(
                token in line.lower()
                for token in ("up", "running", "healthy")
            ) and "exit" not in line.lower()
            services.append(
                ServiceStatus(name=base_name, state=state or line, running=running)
            )
        return services

    def _verify_post_deploy(self, ip: str) -> PostDeployVerification:
        ip_url = f"http://{ip}:4000"
        localhost_url = "http://localhost:4000"
        messages: list[str] = []

        ip_reachable = self._http_reachable(ip_url)
        messages.append(
            f"{ip_url}: {'reachable' if ip_reachable else 'not reachable'}"
        )

        localhost_reachable = self._http_reachable(localhost_url)
        messages.append(
            f"{localhost_url}: {'reachable' if localhost_reachable else 'not reachable'}"
        )

        port_listening = self._port_open("127.0.0.1", 11112) or self._port_open(ip, 11112)
        messages.append(
            f"Port 11112: {'listening' if port_listening else 'not detected'}"
        )

        if not ip_reachable and not localhost_reachable:
            messages.append(
                "PACS UI not reachable — check pacs_ui container, firewall, and wait 1–2 min after start."
            )
        if not port_listening:
            messages.append(
                "Port 11112 not detected — verify CLOUD_PACS_PORT mapping and pacs_core."
            )

        return PostDeployVerification(
            ip_url=ip_url,
            ip_reachable=ip_reachable,
            localhost_url=localhost_url,
            localhost_reachable=localhost_reachable,
            port_11112_listening=port_listening,
            messages=messages,
        )

    @staticmethod
    def _http_reachable(url: str, timeout: int = 8) -> bool:
        try:
            response = requests.get(url, timeout=timeout)
            return response.status_code < 500
        except Exception:
            return False

    @staticmethod
    def _port_open(host: str, port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(2)
                return sock.connect_ex((host, port)) == 0
        except Exception:
            return False

    def open_chrome(self, ip: str) -> bool:
        url = f"http://{ip}:4000"
        chrome_path = find_chrome_path()
        if chrome_path:
            subprocess.Popen([chrome_path, url])
            return True
        webbrowser.open(url)
        return False

    def get_service_log_summary(self) -> str:
        parts: list[str] = []
        for entry in self.logger.entries:
            if entry.step_name.startswith("Fetch logs:"):
                parts.append(f"=== {entry.step_name} ===\n{entry.stdout[-2000:]}")
        return "\n\n".join(parts)
