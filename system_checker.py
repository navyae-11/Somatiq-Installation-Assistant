from __future__ import annotations

import ctypes
import os
import platform
import re
import shutil
import socket
import subprocess
import webbrowser
from pathlib import Path

from backend.config_manager import REQUIRED_PORT, SOMATIQ_DIR, test_write_permission
from backend.models import CheckStatus, SystemCheck

FIREWALL_RULE_NAME = "SOMATIQ"
SKIP_ADAPTER_KEYWORDS = (
    "docker",
    "wsl",
    "virtual",
    "vmware",
    "hyper-v",
    "vethernet",
    "loopback",
    "bluetooth",
    "tunnel",
    "pseudo",
    "tap",
    "npcap",
    "virtualbox",
    "hamachi",
    "default switch",
    "kernel",
    "bridge",
)

DOCKER_DESKTOP_URL = "https://www.docker.com/products/docker-desktop/"
DOCKER_DESKTOP_PATHS = [
    Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    / "Docker"
    / "Docker"
    / "Docker Desktop.exe",
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Docker"
    / "Docker Desktop.exe",
]


def _status(ok: bool, warn: bool = False) -> CheckStatus:
    if ok:
        return CheckStatus.PASSED
    return CheckStatus.WARNING if warn else CheckStatus.FAILED


def _port_free(port: int) -> tuple[bool, str]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        try:
            sock.bind(("0.0.0.0", port))
            return True, "Available"
        except OSError:
            return False, "Already in use"


def _run(cmd: list[str], timeout: int = 12) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _total_ram_gb() -> float:
    try:
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.ullTotalPhys / (1024**3)
    except Exception:
        pass
    return 0.0


def find_chrome_path() -> str | None:
    candidates = [
        os.environ.get("PROGRAMFILES", "") + r"\Google\Chrome\Application\chrome.exe",
        os.environ.get("PROGRAMFILES(X86)", "") + r"\Google\Chrome\Application\chrome.exe",
        os.environ.get("LOCALAPPDATA", "") + r"\Google\Chrome\Application\chrome.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return shutil.which("chrome") or shutil.which("chrome.exe")


def chrome_version(path: str | None) -> str:
    if not path:
        return ""
    try:
        ps_cmd = (
            "& { (Get-Item -LiteralPath '" + path + "').VersionInfo.FileVersion }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=8,
        )
        version = result.stdout.strip()
        if version:
            return "Chrome " + version
    except Exception:
        pass
    try:
        ps_cmd = (
            "& { (Get-Command '" + path + "').FileVersionInfo.FileVersion }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=8,
        )
        version = result.stdout.strip()
        if version:
            return "Chrome " + version
    except Exception:
        pass
    return "Installed"


def find_docker_desktop_path() -> str | None:
    for path in DOCKER_DESKTOP_PATHS:
        if path.exists():
            return str(path)
    return None


def open_docker_desktop() -> bool:
    path = find_docker_desktop_path()
    if path:
        subprocess.Popen([path])
        return True
    return False


def open_docker_install_page() -> None:
    webbrowser.open(DOCKER_DESKTOP_URL)


def download_and_install_docker(timeout: int = 300) -> tuple[bool, str]:
    """Download and silently install Docker Desktop."""
    installer = Path(os.environ.get("TEMP", r"C:\Windows\Temp")) / "DockerDesktopInstaller.exe"
    try:
        subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                f"Invoke-WebRequest -Uri '{DOCKER_DESKTOP_URL}installer-win.exe' "
                f"-OutFile '{installer}' -UseBasicParsing",
            ],
            capture_output=True, text=True, timeout=timeout,
        )
        if not installer.exists():
            return False, "Download failed. Install manually from docker.com."
        proc = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                f"Start-Process -FilePath '{installer}' "
                f"-ArgumentList 'install', '--quiet' -Wait -NoNewWindow",
            ],
            capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode != 0:
            return False, f"Installation failed: {proc.stderr.strip() or 'Unknown error'}"
        return True, "Docker Desktop installed successfully."
    except subprocess.TimeoutExpired:
        return False, "Installation timed out. Install manually from docker.com."
    except Exception as exc:
        return False, str(exc)


def get_os_display() -> str:
    return platform.platform()


def detect_active_ipv4() -> str | None:
    """Detect primary IPv4 from ipconfig, skipping virtual/Docker/WSL adapters."""
    try:
        proc = subprocess.run(
            ["ipconfig"],
            capture_output=True,
            text=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None

    current_adapter = ""
    candidates: list[str] = []
    for raw_line in proc.stdout.splitlines():
        line = raw_line.rstrip()
        if "adapter" in line.lower() and line.strip().endswith(":"):
            current_adapter = line.lower()
            continue
        if "ipv4" not in line.lower() and "ip address" not in line.lower():
            continue
        match = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", line)
        if not match:
            continue
        ip = match.group(1)
        if ip.startswith("127.") or ip.startswith("169.254."):
            continue
        if any(keyword in current_adapter for keyword in SKIP_ADAPTER_KEYWORDS):
            continue
        if ip not in candidates:
            candidates.append(ip)

    if not candidates:
        return None

    def score(ip: str) -> tuple[int, str]:
        parts = [int(p) for p in ip.split(".")]
        if parts[0] == 10:
            return (0, ip)
        if parts[0] == 192 and parts[1] == 168:
            return (1, ip)
        if parts[0] == 172 and 16 <= parts[1] <= 31:
            return (2, ip)
        return (3, ip)

    return sorted(candidates, key=score)[0]


def _firewall_rule_exists() -> bool:
    try:
        proc = subprocess.run(
            [
                "netsh",
                "advfirewall",
                "firewall",
                "show",
                "rule",
                f"name={FIREWALL_RULE_NAME}",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            encoding="utf-8",
            errors="replace",
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return "no rules match" not in output.lower()
    except Exception:
        return False


def ensure_firewall_rule(port: int = REQUIRED_PORT) -> tuple[bool, str]:
    """Create or enable inbound TCP firewall rule SOMATIQ on port 11112."""
    try:
        if _firewall_rule_exists():
            proc = subprocess.run(
                [
                    "netsh",
                    "advfirewall",
                    "firewall",
                    "set",
                    "rule",
                    f"name={FIREWALL_RULE_NAME}",
                    "new",
                    "enable=yes",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                encoding="utf-8",
                errors="replace",
            )
            if proc.returncode == 0:
                return True, f"Rule {FIREWALL_RULE_NAME} enabled (TCP {port})"
            detail = (proc.stderr or proc.stdout or "").strip()[:200]
            return False, detail or "Could not enable firewall rule"

        proc = subprocess.run(
            [
                "netsh",
                "advfirewall",
                "firewall",
                "add",
                "rule",
                f"name={FIREWALL_RULE_NAME}",
                "dir=in",
                "action=allow",
                "protocol=TCP",
                f"localport={port}",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0:
            return True, f"Rule {FIREWALL_RULE_NAME} created (TCP {port})"
        detail = (proc.stderr or proc.stdout or "").strip()[:200]
        return False, detail or "Could not create firewall rule"
    except Exception as exc:
        return False, str(exc)


def format_check_line(check: SystemCheck) -> tuple[str, str | None]:
    """Return (status line, optional fix line)."""
    if check.status == CheckStatus.PASSED:
        icon = "✅"
    elif check.status == CheckStatus.WARNING:
        icon = "⚠️"
    else:
        icon = "❌"

    line = f"{icon} {check.name} - {check.details}"
    fix = None
    if check.status in (CheckStatus.FAILED, CheckStatus.WARNING) and check.suggested_fix:
        fix = f"Fix: {check.suggested_fix}"
    return line, fix


def run_essential_checks() -> list[SystemCheck]:
    """Checks shown on the installer UI."""
    checks: list[SystemCheck] = []

    checks.append(
        SystemCheck(
            id="os",
            name="OS",
            status=CheckStatus.PASSED,
            details=get_os_display(),
        )
    )

    ram_gb = _total_ram_gb()
    ram_fix = ""
    if ram_gb >= 8:
        ram_status, ram_details = CheckStatus.PASSED, f"{ram_gb:.0f} GB available"
    elif ram_gb >= 4:
        ram_status, ram_details = CheckStatus.WARNING, f"{ram_gb:.0f} GB available (8+ GB recommended)"
        ram_fix = "Add RAM or close heavy applications."
    else:
        ram_status, ram_details = CheckStatus.FAILED, f"{ram_gb:.0f} GB available (need 4+ GB)"
        ram_fix = "Upgrade RAM to at least 8 GB."
    checks.append(
        SystemCheck(
            id="ram",
            name="RAM",
            status=ram_status,
            details=ram_details,
            suggested_fix=ram_fix,
        )
    )

    try:
        usage = shutil.disk_usage(SOMATIQ_DIR.parent)
        free_gb = usage.free / (1024**3)
        st_fix = ""
        if free_gb >= 20:
            st_status, st_details = CheckStatus.PASSED, f"{free_gb:.0f} GB free"
        elif free_gb >= 10:
            st_status, st_details = CheckStatus.WARNING, f"{free_gb:.0f} GB free (20+ GB recommended)"
            st_fix = "Free disk space on the Documents drive."
        else:
            st_status, st_details = CheckStatus.FAILED, f"{free_gb:.0f} GB free (need 10+ GB)"
            st_fix = "Free at least 10 GB on the Documents drive."
        checks.append(
            SystemCheck(
                id="storage",
                name="Storage",
                status=st_status,
                details=st_details,
                suggested_fix=st_fix,
            )
        )
    except Exception as exc:
        checks.append(
            SystemCheck(
                id="storage",
                name="Storage",
                status=CheckStatus.WARNING,
                details=str(exc),
                suggested_fix="Verify the Documents folder is accessible.",
            )
        )

    docker_path = shutil.which("docker")
    docker_installed = bool(docker_path)
    docker_running = False
    if docker_path:
        try:
            result = _run(["docker", "info"])
            docker_running = result.returncode == 0
        except Exception:
            docker_running = False

    if docker_installed and docker_running:
        docker_status, docker_details = CheckStatus.PASSED, "Installed · Running"
        docker_fix = ""
    elif docker_installed:
        docker_status, docker_details = CheckStatus.FAILED, "Installed · Not running"
        docker_fix = "Open Docker Desktop and wait until it starts."
    else:
        docker_status, docker_details = CheckStatus.FAILED, "Not installed"
        docker_fix = "Install Docker Desktop for Windows."

    checks.append(
        SystemCheck(
            id="docker",
            name="Docker",
            status=docker_status,
            details=docker_details,
            suggested_fix=docker_fix,
        )
    )
    checks.append(
        SystemCheck(
            id="docker_installed",
            name="Docker",
            status=_status(docker_installed),
            details=docker_details,
            suggested_fix=docker_fix,
        )
    )
    checks.append(
        SystemCheck(
            id="docker_running",
            name="Docker Engine",
            status=_status(docker_running),
            details="Running" if docker_running else "Not running",
            suggested_fix=docker_fix if not docker_running else "",
        )
    )

    try:
        wsl_result = _run(["wsl", "--status"], timeout=10)
        checks.append(
            SystemCheck(
                id="wsl",
                name="WSL",
                status=_status(wsl_result.returncode == 0, warn=wsl_result.returncode != 0),
                details="Installed" if wsl_result.returncode == 0 else "Not installed or not ready",
                suggested_fix="Run: wsl --install (reboot may be required)." if wsl_result.returncode != 0 else "",
            )
        )
    except FileNotFoundError:
        checks.append(
            SystemCheck(
                id="wsl",
                name="WSL",
                status=CheckStatus.FAILED,
                details="Not installed",
                suggested_fix="Run: wsl --install",
            )
        )

    chrome = find_chrome_path()
    version = chrome_version(chrome)
    if chrome and version:
        chrome_details = version
    elif chrome:
        chrome_details = "Installed"
    else:
        chrome_details = "Not installed"
    checks.append(
        SystemCheck(
            id="chrome",
            name="Chrome",
            status=CheckStatus.PASSED if chrome else CheckStatus.WARNING,
            details=chrome_details,
            suggested_fix="Install Google Chrome." if not chrome else "",
        )
    )

    port_free, _port_detail = _port_free(REQUIRED_PORT)
    if not port_free:
        checks.append(
            SystemCheck(
                id="port_11112",
                name="Port 11112",
                status=CheckStatus.FAILED,
                details="Port 11112 is already in use",
                suggested_fix=f"Stop the process using port {REQUIRED_PORT}.",
            )
        )
        checks.append(
            SystemCheck(
                id="firewall_rule",
                name="Firewall Rule",
                status=CheckStatus.FAILED,
                details="Port 11112 is already in use",
                suggested_fix=f"Free port {REQUIRED_PORT} before creating rule SOMATIQ.",
            )
        )
    else:
        checks.append(
            SystemCheck(
                id="port_11112",
                name="Port 11112",
                status=CheckStatus.PASSED,
                details="Available",
            )
        )
        fw_ok, fw_details = ensure_firewall_rule(REQUIRED_PORT)
        checks.append(
            SystemCheck(
                id="firewall_rule",
                name="Firewall Rule",
                status=CheckStatus.PASSED if fw_ok else CheckStatus.WARNING,
                details=fw_details,
                suggested_fix="Run installer as Administrator to create firewall rule SOMATIQ."
                if not fw_ok
                else "",
            )
        )

    writable, write_details = test_write_permission()
    checks.append(
        SystemCheck(
            id="write_perm",
            name="Somatiq folder",
            status=_status(writable),
            details="Write access OK" if writable else write_details,
            suggested_fix="Run as administrator or fix Documents folder permissions."
            if not writable
            else "",
        )
    )

    return checks


# Card order on the System Requirements screen
UI_CHECK_IDS = (
    "os",
    "ram",
    "storage",
    "docker",
    "wsl",
    "chrome",
    "port_11112",
)


def checks_for_ui(checks: list[SystemCheck]) -> list[SystemCheck]:
    by_id = {c.id: c for c in checks}
    return [by_id[cid] for cid in UI_CHECK_IDS if cid in by_id]


def checks_block_deploy(checks: list[SystemCheck]) -> tuple[bool, str]:
    """Return whether deployment should be blocked and why."""
    block_ids = {
        "docker",
        "docker_installed",
        "docker_running",
        "write_perm",
        "port_11112",
        "firewall_rule",
    }
    for check in checks:
        if check.id in block_ids and check.status == CheckStatus.FAILED:
            return True, f"{check.name}: {check.suggested_fix or check.details}"
    return False, ""


def overall_checks_passed(checks: list[SystemCheck]) -> bool:
    ui = checks_for_ui(checks)
    return bool(ui) and all(c.status != CheckStatus.FAILED for c in ui)


def count_by_status(checks: list[SystemCheck]) -> dict[str, int]:
    counts = {s.value: 0 for s in CheckStatus}
    for check in checks:
        counts[check.status.value] = counts.get(check.status.value, 0) + 1
    return counts
