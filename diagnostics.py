from __future__ import annotations

from datetime import datetime
from pathlib import Path

from backend.config_manager import SOMATIQ_DIR
from backend.models import CommandLogEntry, DebugReportData, SystemCheck

FAILURE_PATTERNS: list[tuple[str, str, str, str]] = [
    ("docker: not found", "Docker not installed", "Install Docker Desktop for Windows and restart the PC.", "docker --version"),
    ("'docker' is not recognized", "Docker not installed", "Install Docker Desktop for Windows.", "where docker"),
    ("cannot connect to the docker daemon", "Docker engine not running", "Start Docker Desktop and wait until it shows Running.", "docker info"),
    ("compose: not found", "Docker Compose missing", "Update Docker Desktop to the latest version.", "docker compose version"),
    ("wsl", "WSL issue", "Install or repair WSL: wsl --install", "wsl --status"),
    ("port is already allocated", "Port already in use", "Stop the process using the port or change the compose mapping.", "netstat -ano | findstr :<port>"),
    ("bind: address already in use", "Network binding failure", "Free the conflicting port before deploying.", "netstat -ano"),
    ("invalid compose", "Invalid docker-compose.yml", "Run docker compose config in the Somatiq folder.", "docker compose config"),
    ("pull access denied", "Image pull / registry authentication issue", "Sign in to Google Artifact Registry or check network/proxy.", "docker compose pull"),
    ("unauthorized", "Registry authentication issue", "Run docker login for the artifact registry.", "docker login asia-south1-docker.pkg.dev"),
    ("permission denied", "Permission issue", "Check permissions on ./db and ./storage under Documents\\Somatiq.", "icacls db"),
    ("exited", "Container exited unexpectedly", "Inspect service logs with docker compose logs <service>.", "docker compose ps"),
    ("no such image", "Image pull failure", "Verify network and registry access, then run docker compose pull.", "docker compose pull"),
    ("connection refused", "Service not reachable", "Wait for containers to become healthy or check firewall rules.", "docker compose ps"),
]


def diagnose_failure(entry: CommandLogEntry) -> dict[str, str]:
    blob = f"{entry.stderr}\n{entry.stdout}\n{entry.technical_error}".lower()

    for needle, what_failed, how_to_fix, verify_cmd in FAILURE_PATTERNS:
        if needle in blob:
            return {
                "what_failed": what_failed,
                "possible_reason": (entry.technical_error or entry.stderr or "")[:800],
                "how_to_fix": how_to_fix,
                "verify_command": verify_cmd,
            }

    return {
        "what_failed": entry.step_name,
        "possible_reason": (entry.technical_error or "Unknown error")[:800],
        "how_to_fix": entry.suggested_fix or "Review the deployment terminal output and service logs.",
        "verify_command": "docker compose ps",
    }


def build_debug_report(
    checks: list[SystemCheck],
    config: dict[str, str],
    file_paths: dict[str, object],
    terminal_text: str,
    compose_ps: str,
    service_log_summary: str,
    deployment_error: str = "",
) -> DebugReportData:
    failed = [c.name for c in checks if c.status.value == "Failed"]

    fixes: list[str] = []
    for check in checks:
        if check.suggested_fix and check.suggested_fix not in fixes:
            fixes.append(check.suggested_fix)

    if deployment_error and deployment_error not in fixes:
        fixes.insert(0, deployment_error)

    return DebugReportData(
        timestamp=datetime.now().isoformat(timespec="seconds"),
        system_checks=checks,
        config=config,
        file_paths=file_paths,
        compose_ps=compose_ps,
        service_log_summary=service_log_summary,
        failed_checks=failed,
        suggested_fixes=fixes,
        terminal_text=terminal_text,
        deployment_error=deployment_error,
    )


def format_debug_report_text(report: DebugReportData) -> str:
    lines = [
        "Somatiq PACS Debug Report",
        f"Generated: {report.timestamp}",
        "",
        "=== System Checks ===",
    ]

    for check in report.system_checks:
        lines.append(f"[{check.status.value}] {check.name}")
        lines.append(f"  Details: {check.details}")

        if check.suggested_fix:
            lines.append(f"  Suggested fix: {check.suggested_fix}")

        if check.verify_command:
            lines.append(f"  Verify: {check.verify_command}")

    lines.extend(["", "=== Configuration ==="])

    for key, value in report.config.items():
        lines.append(f"{key}: {value}")

    lines.extend(["", "=== Generated Files ===", str(report.file_paths)])

    deployment_error = getattr(report, "deployment_error", "")

    if deployment_error:
        lines.extend(["", "=== Deployment Error ===", deployment_error])

    if report.failed_checks:
        lines.extend(["", "=== Failed Checks ===", ", ".join(report.failed_checks)])

    if report.suggested_fixes:
        lines.extend(["", "=== Suggested Fixes ==="])
        for idx, fix in enumerate(report.suggested_fixes, 1):
            lines.append(f"{idx}. {fix}")

    lines.extend(["", "=== docker compose ps ===", report.compose_ps or "(not available)"])

    if report.service_log_summary:
        lines.extend(["", "=== Service Logs Summary ===", report.service_log_summary])

    lines.extend(["", "=== Deployment Terminal ===", report.terminal_text or "(empty)"])

    return "\n".join(lines)


def save_debug_report(report: DebugReportData, *, simple: bool = True) -> Path:
    SOMATIQ_DIR.mkdir(parents=True, exist_ok=True)

    path = SOMATIQ_DIR / f"debug-report-{datetime.now():%Y%m%d-%H%M%S}.txt"

    text = format_simple_debug_report(report) if simple else format_debug_report_text(report)

    path.write_text(text, encoding="utf-8")

    return path


def format_simple_debug_report(report: DebugReportData) -> str:
    """Simple user-friendly summary first, technical details after."""

    lines = ["Somatiq Installation Summary", ""]

    for check in report.system_checks:
        if check.status.value == "Passed":
            icon = "✅"
            lines.append(f"{icon} {check.name} check completed")
        elif check.status.value == "Warning":
            icon = "⚠️"
            lines.append(f"{icon} {check.name} - {check.details}")
        else:
            icon = "❌"
            lines.append(f"{icon} {check.name} - {check.details}")

    cfg = report.config

    if cfg.get("ip_address"):
        lines.append("✅ Configuration details added")

    gen = report.file_paths

    if gen:
        if gen.get("somatiq_dir"):
            lines.append("✅ Somatiq folder created")

        if gen.get("env_path"):
            lines.append("✅ .env file created")

        if gen.get("compose_path"):
            lines.append("✅ docker-compose.yml created")

        backups = (gen.get("env_backups") or []) + (gen.get("compose_backups") or [])

        if backups:
            lines.append("✅ Old files backed up")

    deployment_error = getattr(report, "deployment_error", "")

    if deployment_error:
        if "not started" in deployment_error.lower():
            lines.append(f"❌ {deployment_error}")
        else:
            lines.append(f"❌ Deployment failed — {deployment_error}")
    elif cfg.get("ip_address") and gen:
        if report.failed_checks:
            lines.append("⚠️ Deployment needs attention")
        else:
            lines.append("✅ Deployment completed")

    lines.extend(["", "Next action:"])

    next_action = _derive_next_action(report)
    lines.append(next_action)

    lines.extend(["", "--- Technical details ---", ""])

    lines.append(format_debug_report_text(report))

    return "\n".join(lines)


def _derive_next_action(report: DebugReportData) -> str:
    for check in report.system_checks:
        if check.id == "docker_running" and check.status.value == "Failed":
            return "Open Docker Desktop and run deployment again."

        if check.id == "docker_installed" and check.status.value == "Failed":
            return "Install Docker Desktop, then run checks and deployment again."

        if check.id == "write_perm" and check.status.value == "Failed":
            return "Fix Documents\\Somatiq write permission, then try again."

    deployment_error = getattr(report, "deployment_error", "")

    if deployment_error:
        if report.suggested_fixes:
            return report.suggested_fixes[0]

        return deployment_error

    if not report.config.get("ip_address"):
        return "Enter Server IP, Local AE Title, and Client ID."

    if not report.file_paths:
        return "Click Generate Files, then Start Deployment."

    return "Installation complete. Open PACS UI in Chrome if it did not open automatically."