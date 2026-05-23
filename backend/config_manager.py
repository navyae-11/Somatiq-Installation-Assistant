from __future__ import annotations

import re
import shutil
import socket
from datetime import datetime
from pathlib import Path

from backend.models import ConfigValues, GeneratedFiles

APP_TITLE = "Somatiq PACS Installation Assistant"
SOMATIQ_DIR = Path.home() / "Documents" / "Somatiq"
REQUIRED_PORT = 11112
COMPOSE_PORTS = (1112, 4000, 4005, 4006, 4010, 8000, 5431)
REQUIRED_SERVICES = ("db", "pacs_core", "pacs_ui", "viewer_novo")

ENV_TEMPLATE = """POSTGRES_URL=postgresql://pypacs:pypacs@172.16.21.1:5431/pypacs
ACCESS_KEY_ID=---
SECRET_ACCESS_KEY=---
ENDPOINT_URL=---
BUCKET_NAME=somatiq-pacs
ONLINE_STORAGE=local
DICOM_COMPRESSION=JPEGLS
API_PORT=8000
DICOM_PORT=1112
STORAGE_AE_TITLES=SOMATIQ
WORKLIST_AE_TITLES=SOMAMWL
STORAGE_PORT=4002
WORKLIST_PORT=4003
DEFAULT_ROUTE=storage
STORE_WORKERS=4
VIEWER_PORT=4010
JWT_SECRET_KEY=trustwell-production
CLOUD_PACS_HOST=archive.dcm.somatiq.ai
CLOUD_PACS_PORT=11112
CLOUD_PACS_AE_TITLE = DCM4CHEE
LOCAL_AE_TITLE=SQ-TRUST-WL
DEPLOYMENT_TYPE=local
HOST=172.16.21.1
SERVER_ENDPOINT=https://api.ris.somatiq.ai/api/v1
INTEGRATION_SECRET_KEY=6KlVGeuVwvAskiR0qelBoJt767mDJnNp
CLIENT_ID=92
MPPS_WEBHOOK_ENABLED=true
MWL_INTERVAL = 30
METRICS_INTERVAL = 60
STORAGE_BASE=storage/
CACHE_MAX_AGE_HOURS=5
STORAGE_INTERVAL= 30000

#viewer
VITE_DICOMWEB_PORT=4005
VITE_DICOMWEB_USERNAME=viewer
VITE_DICOMWEB_PASSWORD=N0v0@26
"""

DOCKER_COMPOSE_TEMPLATE = """services:
  db:
    image: asia-south1-docker.pkg.dev/iq-somat/somatiq/soma-db:v0.2.1
    restart: always
    ports:
      - "5431:5432"
    environment:
      - TZ=Asia/Kolkata
      - PGTZ=Asia/Kolkata
    command:
      - postgres
      - -c
      - shared_buffers=4GB
      - -c
      - work_mem=128MB
      - -c
      - maintenance_work_mem=1GB
      - -c
      - max_wal_size=4GB
      - -c
      - checkpoint_completion_target=0.9
      - -c
      - max_connections=200
      - -c
      - effective_cache_size=8GB
    volumes:
      - ./db:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U pypacs"]
      interval: 5s
      timeout: 5s
      retries: 5

  pacs_core:
    image: asia-south1-docker.pkg.dev/iq-somat/somatiq/soma-pacs:v5.0.5
    restart: always
    privileged: true  # Required for NAS/CIFS mounting
    ports:
      - "4006:4006"
      - "1112:1112"
      - "4005:4005"
      - "8000:8000"
    environment:
      - TZ=Asia/Kolkata
      - POSTGRES_URL=postgresql://pypacs:pypacs@db:5432/pypacs
      - STORAGE_BASE=/app/storage
    env_file:
      - .env
    volumes:
      - ./storage:/app/storage
      - ./logs:/app/logs
    depends_on:
      db:
        condition: service_healthy

  pacs_ui:
    image: asia-south1-docker.pkg.dev/iq-somat/somatiq/soma-ui:v0.5.4
    restart: always
    ports:
      - "4000:80"
    environment:
      - TZ=Asia/Kolkata
      - VITE_API_PORT=${API_PORT:-8000}
      - VITE_VIEWER_PORT=${VIEWER_PORT:-4010}
    depends_on:
      - pacs_core

  viewer_novo:
    image: asia-south1-docker.pkg.dev/iq-somat/somatiq/novo:v6.4
    restart: always
    environment:
      - TZ=Asia/Kolkata
      - DEPLOYMENT_TYPE=local
      - DICOMWEB_PORT=${VITE_DICOMWEB_PORT:-4005}
      - DICOMWEB_USERNAME=${VITE_DICOMWEB_USERNAME}
      - DICOMWEB_PASSWORD=${VITE_DICOMWEB_PASSWORD}
    command:
      - /bin/sh
      - -c
      - |
        echo "window.RUNTIME_CONFIG={DEPLOYMENT_TYPE:'$$DEPLOYMENT_TYPE',URL:'',PORT:'$$DICOMWEB_PORT',USERNAME:'$$DICOMWEB_USERNAME',PASSWORD:'$$DICOMWEB_PASSWORD'};" > /usr/share/nginx/html/config.js
        cat /usr/share/nginx/html/config.js
        nginx -g 'daemon off;'
    ports:
      - "4010:80"
"""


def validate_ipv4(ip: str) -> bool:
    try:
        socket.inet_aton(ip.strip())
        parts = ip.strip().split(".")
        return len(parts) == 4 and all(0 <= int(p) <= 255 for p in parts)
    except (OSError, ValueError):
        return False


def validate_config(ip: str, ae_title: str, client_id: str) -> tuple[bool, str]:
    ip = ip.strip()
    ae_title = ae_title.strip()
    client_id = client_id.strip()
    if not ip or not ae_title or not client_id:
        return False, "IP Address, Local AE Title, and Client ID are required."
    if not validate_ipv4(ip):
        return False, "Enter a valid IPv4 address (e.g. 192.168.1.10)."
    if " " in ae_title:
        return False, "Local AE Title must not contain spaces."
    if not client_id.isdigit():
        return False, "Client ID must be numeric."
    return True, ""


def build_env_content(values: ConfigValues) -> str:
    lines: list[str] = []
    for line in ENV_TEMPLATE.splitlines():
        if line.startswith("POSTGRES_URL="):
            line = re.sub(
                r"(@)([^:/]+)(:5431/pypacs)",
                rf"\g<1>{values.ip_address}\g<3>",
                line,
            )
        elif line.startswith("HOST="):
            line = f"HOST={values.ip_address}"
        elif line.startswith("LOCAL_AE_TITLE="):
            line = f"LOCAL_AE_TITLE={values.local_ae_title}"
        elif line.startswith("CLIENT_ID="):
            line = f"CLIENT_ID={values.client_id}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def _backup_if_exists(path: Path) -> str | None:
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.backup-{stamp}")
    shutil.copy2(path, backup)
    return str(backup)


def generate_files(values: ConfigValues) -> tuple[GeneratedFiles, str]:
    SOMATIQ_DIR.mkdir(parents=True, exist_ok=True)
    env_path = SOMATIQ_DIR / ".env"
    compose_path = SOMATIQ_DIR / "docker-compose.yml"

    env_backup = _backup_if_exists(env_path)
    compose_backup = _backup_if_exists(compose_path)

    env_content = build_env_content(values)
    env_path.write_text(env_content, encoding="utf-8")
    compose_path.write_text(DOCKER_COMPOSE_TEMPLATE, encoding="utf-8")

    meta = GeneratedFiles(
        somatiq_dir=str(SOMATIQ_DIR),
        env_path=str(env_path),
        compose_path=str(compose_path),
        env_backups=[b for b in [env_backup] if b],
        compose_backups=[b for b in [compose_backup] if b],
    )
    return meta, env_content


def test_write_permission() -> tuple[bool, str]:
    try:
        SOMATIQ_DIR.mkdir(parents=True, exist_ok=True)
        probe = SOMATIQ_DIR / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, f"Writable: {SOMATIQ_DIR}"
    except Exception as exc:
        return False, str(exc)


def confirmation_summary(values: ConfigValues) -> dict[str, str]:
    return {
        "IP Address": values.ip_address,
        "Local AE Title": values.local_ae_title,
        "Client ID": values.client_id,
        "HOST": values.ip_address,
        "POSTGRES_URL": (
            f"postgresql://pypacs:pypacs@{values.ip_address}:5431/pypacs"
        ),
        "PACS UI URL": f"http://{values.ip_address}:4000",
    }
