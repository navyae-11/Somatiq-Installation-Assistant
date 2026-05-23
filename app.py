"""Somatiq PACS Installation Assistant — dark themed setup UI."""

from __future__ import annotations

import html
import os
import subprocess
import threading
import time
from datetime import timedelta

import streamlit as st

from backend.config_manager import APP_TITLE, SOMATIQ_DIR, ConfigValues, generate_files, validate_config
from backend.debug_logger import DebugLogger
from backend.docker_manager import DockerManager
from backend.models import CheckStatus, DeployStep, GeneratedFiles, SystemCheck
from backend.system_checker import (
    checks_block_deploy,
    checks_for_ui,
    detect_active_ipv4,
    download_and_install_docker,
    open_docker_desktop,
    open_docker_install_page,
    overall_checks_passed,
    run_essential_checks,
)

STEPS = [
    "System Requirements",
    "Configuration",
    "Generate Files",
    "Deployment",
    "Status",
    "Final Logs",
]

THEME_CSS = """
<style>
    .stApp {
        background: linear-gradient(160deg, #0b0e14 0%, #0f172a 45%, #0b1220 100%);
    }
    [data-testid="stSidebar"], [data-testid="collapsedControl"] { display: none; }
    .block-container { padding: 1.25rem 2rem 2rem; max-width: 100%; }

    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(12px); }
        to { opacity: 1; transform: translateY(0); }
    }
    @keyframes slideIn {
        from { opacity: 0; transform: translateX(-20px); }
        to { opacity: 1; transform: translateX(0); }
    }
    @keyframes pulseGlow {
        0%, 100% { box-shadow: 0 0 4px rgba(59,130,246,.2); }
        50% { box-shadow: 0 0 12px rgba(59,130,246,.5); }
    }



    /* ── top progress bar ── */
    .top-progress {
        display: flex; align-items: center; gap: 0.5rem;
        margin-bottom: 1.25rem; padding: 0.25rem 0;
        animation: fadeIn .3s ease-out;
    }
    .top-progress-step {
        display: flex; align-items: center; gap: 0.35rem;
        font-size: 0.78rem; white-space: nowrap;
        transition: all .3s ease;
    }
    .top-progress-step .step-circle {
        width: 22px; height: 22px; border-radius: 50%;
        display: flex; align-items: center; justify-content: center;
        font-size: 0.65rem; font-weight: 700;
        border: 2px solid #334155; color: #64748b;
        background: #0f172a; transition: all .35s ease;
    }
    .top-progress-step.active .step-circle {
        border-color: #3b82f6; background: #1e3a5f; color: #93c5fd;
        animation: pulseGlow 1.5s infinite;
    }
    .top-progress-step.done .step-circle {
        border-color: #4ade80; background: #14532d; color: #4ade80;
    }
    .top-progress-step.fail .step-circle {
        border-color: #f87171; background: #450a0a; color: #f87171;
    }
    .top-progress-step .step-label { color: #64748b; transition: color .3s; }
    .top-progress-step.active .step-label { color: #93c5fd; }
    .top-progress-step.done .step-label { color: #4ade80; }
    .top-progress-connector {
        width: 28px; height: 2px; background: #334155; flex-shrink: 0;
        transition: background .35s ease;
    }
    .top-progress-connector.done { background: #4ade80; }
    .top-progress-connector.fail { background: #f87171; }

    .side-tabs {
        width: 180px; min-width: 180px; flex-shrink: 0;
        display: flex; flex-direction: column; gap: 0.3rem;
        animation: slideIn .35s ease-out; padding: 0.5rem 0;
    }
    /* vertical separator between left sidebar and right content */
    [data-testid="stHorizontalBlock"] > div:first-child {
        border-right: 1px solid rgba(255, 255, 255, 0.15) !important;
        padding-right: 1.5rem !important;
    }
    /* gap between all column groups (main layout + inner cards) */
    div[data-testid="stHorizontalBlock"] { gap: 1.5rem; }
    .side-tab {
        display: flex; align-items: center; gap: 0.6rem;
        background: transparent; border: 1px solid transparent;
        border-radius: 10px; padding: 0.6rem 0.75rem;
        color: #94a3b8; cursor: pointer; transition: all .2s;
        font-size: 0.85rem; width: 100%;
    }
    .side-tab:hover { background: #1e293b; color: #cbd5e1; }
    .side-tab.active {
        background: #172554; border-color: #3b82f6;
        color: #f8fafc; font-weight: 600;
        box-shadow: 0 0 0 1px #2563eb33;
    }
    .side-tab .tab-icon {
        width: 24px; height: 24px; border-radius: 50%;
        display: flex; align-items: center; justify-content: center;
        font-size: 0.65rem; font-weight: 700; flex-shrink: 0;
        border: 2px solid #334155; background: #0f172a;
        transition: all .35s;
    }
    .side-tab.active .tab-icon {
        border-color: #3b82f6; background: #1e3a5f; color: #93c5fd;
    }
    .side-tab.done .tab-icon {
        border-color: #4ade80; background: #14532d; color: #4ade80;
    }
    .side-tab.fail .tab-icon {
        border-color: #f87171; background: #450a0a; color: #f87171;
    }
    .side-tab .tab-label { flex: 1; }
    .side-tab .tab-badge {
        font-size: 0.6rem; padding: 0.1rem 0.4rem; border-radius: 999px;
    }
    .tab-badge.done { background: #14532d; color: #4ade80; }
    .tab-badge.fail { background: #450a0a; color: #f87171; }
    .tab-badge.running { background: #1e3a5f; color: #93c5fd; }

    /* side-tab as actual Streamlit button override */
    div[data-testid^="stButton"] button.step-tab-btn {
        display: flex; align-items: center; gap: 0.6rem;
        background: transparent; border: 1px solid transparent;
        border-radius: 10px; padding: 0.6rem 0.75rem;
        color: #94a3b8; font-size: 0.85rem; width: 100%;
        transition: all .2s; cursor: pointer; justify-content: flex-start;
    }
    div[data-testid^="stButton"] button.step-tab-btn:hover {
        background: #1e293b; color: #cbd5e1; border-color: #1e293b;
    }
    div[data-testid^="stButton"] button.step-tab-btn.active {
        background: #172554; border-color: #3b82f6;
        color: #f8fafc; font-weight: 600;
        box-shadow: 0 0 0 1px #2563eb33;
    }
    div[data-testid^="stButton"] button.step-tab-btn p { margin: 0; }
    .st-emotion-cache-wyoiad { display: none !important; }

    /* side tab button icons */
    button[data-testid*="stab_"] { font-size: 0.85rem; letter-spacing: 0.01em; }

    .page-title { color: #f8fafc; font-size: 1.45rem; font-weight: 700; margin: 0 0 0.25rem; }
    .page-sub { color: #94a3b8; font-size: 0.9rem; margin-bottom: 1.25rem; }
    .req-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
    @media (max-width: 900px) {
        .req-grid { grid-template-columns: 1fr; }
        .side-tabs { width: 100%; flex-direction: row; flex-wrap: wrap; }
        .side-tab { width: auto; flex: 1; min-width: 120px; }
        .top-progress { gap: 0.25rem; font-size: 0.7rem; }
        .top-progress-connector { width: 16px; }
        .block-container { padding: 1rem; }
    }
    .req-card {
        background: #0f172a; border: 1px solid #1e293b; border-radius: 10px;
        padding: 0.85rem 1rem; display: flex; align-items: center; justify-content: space-between;
        animation: fadeIn .4s ease-out both;
        transition: border-color .3s, box-shadow .3s;
        margin-bottom: 10px;
    }
    .req-card:last-of-type { margin-bottom: 0; }
    .req-card:hover { border-color: #3b82f6; box-shadow: 0 0 8px rgba(59,130,246,.15); }
    .req-name { color: #e2e8f0; font-weight: 600; font-size: 0.92rem; }
    .req-val { color: #94a3b8; font-size: 0.8rem; margin-top: 0.2rem; word-break: break-all; }
    .badge-pass {
        background: #14532d; color: #4ade80; border: 1px solid #166534;
        border-radius: 999px; padding: 0.2rem 0.65rem; font-size: 0.75rem; font-weight: 600;
    }
    .badge-fail {
        background: #450a0a; color: #f87171; border: 1px solid #7f1d1d;
        border-radius: 999px; padding: 0.2rem 0.65rem; font-size: 0.75rem; font-weight: 600;
    }
    .badge-warn {
        background: #422006; color: #fbbf24; border: 1px solid #78350f;
        border-radius: 999px; padding: 0.2rem 0.65rem; font-size: 0.75rem; font-weight: 600;
    }
    .fix-hint { color: #fbbf24; font-size: 0.82rem; margin-top: 0.35rem; }
    .footer-bar {
        margin-top: 1.25rem; padding-top: 1rem; border-top: 1px solid #1e293b;
        display: flex; gap: 0.75rem; flex-wrap: wrap;
    }
    .status-banner {
        background: #052e16; border: 1px solid #166534; color: #86efac;
        border-radius: 8px; padding: 0.65rem 1rem; margin-top: 1rem; font-size: 0.9rem;
        animation: fadeIn .35s ease-out;
    }
    .status-banner.fail {
        background: #450a0a; border-color: #7f1d1d; color: #fca5a5;
    }
    .file-line { color: #cbd5e1; font-size: 0.9rem; margin: 0.35rem 0; animation: fadeIn .3s ease-out both; }
    .deploy-line { color: #cbd5e1; font-size: 0.9rem; margin: 0.4rem 0; animation: fadeIn .3s ease-out both; }
    .stButton > button[kind="primary"] {
        background: linear-gradient(90deg, #2563eb, #3b82f6);
        border: none; color: white; font-weight: 600;
        transition: all .25s;
    }
    .stButton > button[kind="primary"]:hover:not(:disabled) {
        filter: brightness(1.15);
        box-shadow: 0 4px 15px rgba(59,130,246,.35);
    }

    .status-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
    @media (max-width: 900px) { .status-grid { grid-template-columns: 1fr; } }
    .status-card {
        background: #0f172a; border: 1px solid #1e293b; border-radius: 10px;
        padding: 1rem; animation: fadeIn .4s ease-out both;
    }
    .status-card h3 { color: #93c5fd; font-size: 0.85rem; margin: 0 0 0.6rem; }
    .status-card .stat-row {
        display: flex; justify-content: space-between; align-items: center;
        padding: 0.35rem 0; border-bottom: 1px solid #1e293b;
        font-size: 0.82rem;
    }
    .status-card .stat-row:last-child { border-bottom: none; }
    .status-card .stat-label { color: #94a3b8; }
    .status-card .stat-val { color: #e2e8f0; font-weight: 500; }
</style>
"""


def init_state() -> None:
    defaults = {
        "active_step": 0,
        "checks": [],
        "checks_ran": False,
        "config": {"ip_address": "", "local_ae_title": "", "client_id": ""},
        "config_ok": False,
        "generated_files": None,
        "file_lines": [],
        "logger": DebugLogger(),
        "docker_manager": None,
        "deploy_thread": None,
        "deploy_done": None,
        "ip_auto_filled": False,
        "auto_mode": False,
        "docker_auto_resolved": False,
        "auto_deploy_triggered": False,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val
    if st.session_state.docker_manager is None:
        st.session_state.docker_manager = DockerManager(st.session_state.logger)
    if not st.session_state.ip_auto_filled:
        detected = detect_active_ipv4()
        if detected and not st.session_state.config.get("ip_address"):
            st.session_state.config["ip_address"] = detected
        st.session_state.ip_auto_filled = True


def step_status(index: int) -> tuple[str, str]:
    if index == 0:
        if not st.session_state.checks_ran:
            return ("Pending", "")
        if overall_checks_passed(st.session_state.checks):
            return ("Completed", "done")
        return ("Needs attention", "fail")
    if index == 1:
        return ("Completed", "done") if st.session_state.config_ok else ("Pending", "")
    if index == 2:
        return ("Completed", "done") if st.session_state.generated_files else ("Pending", "")
    if index == 3:
        if st.session_state.deploy_done is True:
            return ("Completed", "done")
        if st.session_state.deploy_done is False:
            return ("Failed", "fail")
        dm = st.session_state.docker_manager
        if dm.state.running:
            return ("Running", "running")
        return ("Pending", "")
    if index == 4:
        all_done = all(
            step_status(i)[0] in ("Completed", "All good")
            for i in range(4)
        )
        return ("All good", "done") if all_done else ("In progress", "running")
    if index == 5:
        return ("Available", "done") if st.session_state.logger.entries else ("No logs", "")
    return ("Pending", "")


def badge_class(status: CheckStatus) -> str:
    if status == CheckStatus.PASSED:
        return "badge-pass"
    if status == CheckStatus.WARNING:
        return "badge-warn"
    return "badge-fail"


def badge_label(status: CheckStatus) -> str:
    if status == CheckStatus.PASSED:
        return "Pass"
    if status == CheckStatus.WARNING:
        return "Warn"
    return "Fail"


def render_top_progress() -> None:
    # Only show first 4 steps (exclude Status, Final Logs)
    show_steps = STEPS[:4]
    parts = []
    for i, name in enumerate(show_steps):
        label, badge = step_status(i)
        state = badge or ("active" if i == st.session_state.active_step else "")
        check = "&#10003;" if state == "done" else str(i + 1)
        parts.append(
            '<div class="top-progress-step %s">'
            '<span class="step-circle">%s</span>'
            '<span class="step-label">%s</span></div>'
            % (state, check, name)
        )
        if i < len(STEPS) - 1:
            conn = "done" if state == "done" else ("fail" if badge == "fail" else "")
            parts.append('<div class="top-progress-connector %s"></div>' % conn)
    st.markdown(
        '<div class="top-progress">' + "".join(parts) + "</div>",
        unsafe_allow_html=True,
    )


def render_side_tabs() -> None:
    icons = ["\u22A1", "\u2699", "\u229E", "\u25B8", "\u2261", "\u2630"]
    for i, name in enumerate(STEPS):
        label, badge = step_status(i)
        icon_label = "%s %s" % (icons[i], name)
        if badge:
            icon_label += " [%s]" % label
        if st.button(icon_label, key="stab_%d" % i, use_container_width=True,
                     type="primary" if i == st.session_state.active_step else "secondary"):
            st.session_state.active_step = i
            st.rerun()


def auto_advance() -> None:
    if not st.session_state.auto_mode:
        return
    step = st.session_state.active_step
    if step == 0 and st.session_state.checks_ran:
        if overall_checks_passed(st.session_state.checks):
            st.session_state.active_step = 1
            st.rerun()
    elif step == 1 and st.session_state.config_ok:
        st.session_state.active_step = 2
        st.rerun()
    elif step == 2 and st.session_state.generated_files:
        st.session_state.active_step = 3
        if not st.session_state.auto_deploy_triggered:
            st.session_state.auto_deploy_triggered = True
            _start_deployment()
        st.rerun()


def _start_deployment() -> None:
    dm = st.session_state.docker_manager
    st.session_state.deploy_done = None
    st.session_state.logger.clear()
    ip_addr = st.session_state.config.get("ip_address", "").strip()

    def on_complete(success: bool) -> None:
        st.session_state.deploy_done = success

    thread = threading.Thread(
        target=lambda: dm.deploy(ip_addr, on_complete=on_complete),
        daemon=True,
    )
    st.session_state.deploy_thread = thread
    thread.start()


def _render_req_card(check: SystemCheck, index: int) -> None:
    bc = badge_class(check.status)
    bl = badge_label(check.status)
    delay = (hash(check.id) % 5) * 0.06
    name = html.escape(check.name)
    details = html.escape(check.details)
    fix = ""
    if check.status != CheckStatus.PASSED and check.suggested_fix:
        fix = '<div class="fix-hint">Fix: %s</div>' % html.escape(check.suggested_fix)
    card = (
        '<div class="req-card" style="animation-delay:%.2fs">'
        '<div>'
        '<div class="req-name">%s</div>'
        '<div class="req-val">%s</div>%s'
        "</div>"
        '<span class="%s">%s</span>'
        "</div>"
    ) % (delay, name, details, fix, bc, bl)
    st.markdown(card, unsafe_allow_html=True)


def filter_logs(text: str) -> str:
    if not text:
        return "(no logs yet)"
    keywords = (
        "pull", "up -d", "compose", "image", "container",
        "error", "failed", "exit=", "starting", "started",
        "downloading", "pacs", "info", "check", "wsl", "docker",
        "firewall", "port", "installed", "running",
    )
    lines = text.splitlines()
    kept = [ln for ln in lines if any(k in ln.lower() for k in keywords) or ln.startswith("[")]
    return "\n".join(kept) if kept else text


def render_footer(current_step: int) -> None:
    st.markdown('<div class="footer-bar">', unsafe_allow_html=True)
    if current_step == 3:
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Open Somatiq Folder", use_container_width=True):
                SOMATIQ_DIR.mkdir(parents=True, exist_ok=True)
                os.startfile(SOMATIQ_DIR)
    st.markdown("</div>", unsafe_allow_html=True)


def _docker_daemon_ready(timeout: int = 15) -> bool:
    """Poll Docker daemon until responsive or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        time.sleep(2)
    return False


def _auto_handle_docker(checks: list) -> None:
    if st.session_state.docker_auto_resolved:
        return
    dc = next((c for c in checks if c.id == "docker"), None)
    if not dc or dc.status == CheckStatus.PASSED:
        st.session_state.docker_auto_resolved = True
        return
    if "Not installed" in dc.details:
        with st.status("Installing Docker Desktop...", expanded=True) as s:
            s.write("Downloading and installing (may take a few minutes)...")
            ok, msg = download_and_install_docker()
            if ok:
                s.update(label="Docker Desktop installed!", state="complete")
                st.session_state.docker_auto_resolved = True
            else:
                s.update(label="Installation failed", state="error")
                st.warning(msg)
    elif "Not running" in dc.details:
        with st.spinner("Starting Docker Desktop..."):
            if open_docker_desktop():
                ready = _docker_daemon_ready()
                if ready:
                    st.success("Docker daemon is now responsive.")
                else:
                    st.info("Docker Desktop launched. It may still be starting up.")
            else:
                st.warning("Docker Desktop executable not found. Please start it manually.")
        st.session_state.docker_auto_resolved = True


def panel_system_requirements() -> None:
    st.markdown('<p class="page-title">System Requirements</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="page-sub">Verify this machine is ready before configuration and deployment.</p>',
        unsafe_allow_html=True,
    )

    running = bool(
        st.session_state.deploy_thread
        and st.session_state.deploy_thread.is_alive()
    )

    c1, c2, c3 = st.columns([1, 1, 1])
    with c2:
        if st.button("Check Requirements", type="primary", use_container_width=True,
                     disabled=running):
            st.session_state._retried_checks = False
            st.session_state.checks = run_essential_checks()
            st.session_state.checks_ran = True
            st.rerun()
    with c3:
        auto_disabled = running or (
            st.session_state.checks_ran
            and not overall_checks_passed(st.session_state.checks)
        )
        if st.button("Start Full Automation", use_container_width=True,
                     disabled=auto_disabled,
                     help="Run all steps automatically through deployment"):
            st.session_state.checks = run_essential_checks()
            st.session_state.checks_ran = True
            st.session_state.auto_mode = True
            st.rerun()

    checks = st.session_state.checks
    if checks:
        ui_checks = checks_for_ui(checks)
        for i, c in enumerate(ui_checks):
            if i % 2 == 0:
                cols = st.columns(2)
            with cols[i % 2]:
                _render_req_card(c, i)

        dc = next((c for c in checks if c.id == "docker"), None)
        if dc and dc.status == CheckStatus.FAILED:
            b1, b2 = st.columns(2)
            if "Not installed" in dc.details and b1.button("Install Docker Desktop"):
                open_docker_install_page()
            if "Not running" in dc.details and b2.button("Open Docker Desktop"):
                if not open_docker_desktop():
                    st.warning("Docker Desktop executable not found.")

        if overall_checks_passed(checks):
            st.markdown(
                '<div class="status-banner">&#10003; System requirements check completed successfully.</div>',
                unsafe_allow_html=True,
            )
            if st.session_state.auto_mode:
                auto_advance()
        else:
            _auto_handle_docker(checks)
            if st.session_state.auto_mode and not st.session_state.get("_retried_checks"):
                st.session_state._retried_checks = True
                st.session_state.checks = run_essential_checks()
                st.session_state.checks_ran = True
                st.rerun()
            if not running:
                st.markdown(
                    '<div class="status-banner fail">&#9888; Some requirements need attention.</div>',
                    unsafe_allow_html=True,
                )


def panel_configuration() -> None:
    st.markdown('<p class="page-title">Configuration</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="page-sub">Server IP is auto-detected from your network adapter. Edit any field as needed.</p>',
        unsafe_allow_html=True,
    )

    cfg = st.session_state.config
    ip = st.text_input("Server IP Address", value=cfg.get("ip_address", ""),
                       placeholder="e.g. 192.168.1.10")
    ae = st.text_input("Local AE Title", value=cfg.get("local_ae_title", ""),
                       placeholder="AE title (e.g. SQ-TRUST-WL)")
    cid = st.text_input("Client ID", value=cfg.get("client_id", ""),
                        placeholder="Client ID (e.g. 92)")

    if st.button("Save Configuration", type="primary", use_container_width=True):
        st.session_state.config = {"ip_address": ip, "local_ae_title": ae, "client_id": cid}
        ok, err = validate_config(ip, ae, cid)
        st.session_state.config_ok = ok
        if ok:
            st.rerun()

    ok, err = validate_config(ip, ae, cid)
    if st.session_state.config_ok:
        st.markdown(
            '<div class="status-banner">&#10003; Configuration saved</div>',
            unsafe_allow_html=True,
        )
        if st.session_state.auto_mode:
            auto_advance()
    elif any([ip.strip(), ae.strip(), cid.strip()]):
        st.markdown('<p class="fix-hint">Fix: %s</p>' % html.escape(err), unsafe_allow_html=True)


def _do_generate_files() -> None:
    cfg = st.session_state.config
    values = ConfigValues(
        cfg["ip_address"].strip(),
        cfg["local_ae_title"].strip(),
        cfg["client_id"].strip(),
    )
    meta, _ = generate_files(values)
    st.session_state.generated_files = meta
    lines = [
        "&#10003; Somatiq folder created",
        "&#10003; .env file created",
        "&#10003; docker-compose.yml created",
    ]
    if meta.env_backups or meta.compose_backups:
        lines.append("&#10003; Old files backed up")
    st.session_state.file_lines = lines


def panel_generate_files() -> None:
    st.markdown('<p class="page-title">Generate Files</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="page-sub">Creates folder <code>%s</code> with .env and docker-compose.yml.</p>'
        % SOMATIQ_DIR,
        unsafe_allow_html=True,
    )

    ok = st.session_state.config_ok
    if st.button("Generate Files", type="primary", disabled=not ok):
        _do_generate_files()
        st.rerun()

    if st.session_state.auto_mode and ok and not st.session_state.generated_files:
        _do_generate_files()
        st.rerun()

    for line in st.session_state.file_lines:
        st.markdown('<p class="file-line">%s</p>' % line, unsafe_allow_html=True)

    if st.session_state.generated_files:
        st.markdown(
            '<div class="status-banner">&#10003; Files generated successfully.</div>',
            unsafe_allow_html=True,
        )
        if st.session_state.auto_mode:
            auto_advance()


def format_deploy_line(step: DeployStep) -> tuple[str, str, str]:
    if step.status == "running":
        return ("&#128260;", step.label, "")
    if step.status == "done":
        labels = {
            "validate": "Compose file valid",
            "pull": "Docker images pulled",
            "start": "Containers started",
            "status": "PACS is running",
        }
        return ("&#10003;", labels.get(step.id, step.label), "")
    if step.status == "failed":
        fix = step.fix or "See logs."
        return ("&#10007;", "Failed at: " + step.label, fix)
    return ("", "", "")


def render_deploy_steps() -> None:
    dm = st.session_state.docker_manager
    for step in dm.state.steps:
        if step.status == "pending":
            continue
        icon, label, fix = format_deploy_line(step)
        if label:
            st.markdown('<p class="deploy-line">%s %s</p>' % (icon, label), unsafe_allow_html=True)
        if fix:
            st.markdown('<p class="fix-hint">Fix: %s</p>' % fix, unsafe_allow_html=True)
    if dm.state.running:
        st.markdown('<p class="deploy-line">&#128260; Deployment in progress…</p>', unsafe_allow_html=True)


@st.fragment(run_every=timedelta(seconds=1))
def refresh_deployment_panel() -> None:
    render_deploy_steps()


def panel_deployment() -> None:
    st.markdown('<p class="page-title">Deployment</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="page-sub">Pull images, start containers, and verify PACS.</p>',
        unsafe_allow_html=True,
    )

    # Check if deploy thread finished and update state
    thread = st.session_state.deploy_thread
    if thread and not thread.is_alive() and st.session_state.deploy_done is None:
        dm2 = st.session_state.docker_manager
        st.session_state.deploy_done = dm2.state.success

    # Re-run Docker check if auto-resolved, so stale "Not running" doesn't linger
    if st.session_state.docker_auto_resolved:
        fresh = run_essential_checks()
        for c in fresh:
            if c.id == "docker":
                old = next((x for x in st.session_state.checks if x.id == "docker"), None)
                if old and old.status != c.status:
                    st.session_state.checks = fresh
                    break

    checks = st.session_state.checks
    blocked, block_msg = checks_block_deploy(checks) if checks else (True, "Run system requirements first.")
    thread_alive = thread and thread.is_alive()
    can_deploy = (
        st.session_state.config_ok
        and st.session_state.generated_files
        and not blocked
        and not thread_alive
    )

    dm = st.session_state.docker_manager

    if st.button("Start Deployment", type="primary", disabled=not can_deploy):
        st.session_state.auto_deploy_triggered = True
        _start_deployment()
        st.rerun()

    if (
        st.session_state.auto_mode
        and not st.session_state.auto_deploy_triggered
        and can_deploy
        and not thread_alive
        and not dm.state.running
        and st.session_state.deploy_done is not True
    ):
        st.session_state.auto_deploy_triggered = True
        _start_deployment()
        st.rerun()

    if blocked and checks:
        st.markdown('<p class="fix-hint">Fix: %s</p>' % html.escape(block_msg), unsafe_allow_html=True)

    if dm.state.running or thread_alive:
        refresh_deployment_panel()
    elif dm.state.steps:
        render_deploy_steps()

    if st.session_state.deploy_done is True:
        ip = st.session_state.config.get("ip_address", "127.0.0.1")
        st.markdown(
            '<div class="status-banner">&#10003; Deployment complete — PACS running at http://%s:4000</div>' % ip,
            unsafe_allow_html=True,
        )
    elif st.session_state.deploy_done is False:
        err = dm.state.last_error or "Deployment failed"
        st.markdown(
            '<div class="status-banner fail">&#10007; %s</div>' % html.escape(err),
            unsafe_allow_html=True,
        )


def panel_final_logs() -> None:
    st.markdown('<p class="page-title">Final Logs</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="page-sub">Complete detailed logs of the entire process for debugging.</p>',
        unsafe_allow_html=True,
    )
    raw = st.session_state.logger.format_terminal()
    if not raw.strip():
        st.caption("No logs recorded yet. Run checks or deployment first.")
    else:
        st.code(raw, language="text", line_numbers=True)


def panel_status() -> None:
    st.markdown('<p class="page-title">Status Dashboard</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="page-sub">Overview of the entire installation process.</p>',
        unsafe_allow_html=True,
    )

    dm = st.session_state.docker_manager

    st.markdown('<div class="status-grid">', unsafe_allow_html=True)

    # System Requirements card
    checks_rows = ""
    if st.session_state.checks:
        ui_checks = checks_for_ui(st.session_state.checks)
        for c in ui_checks:
            bc = badge_class(c.status)
            bl = badge_label(c.status)
            checks_rows += (
                '<div class="stat-row">'
                '<span class="stat-label">%s</span>'
                '<span class="%s">%s</span></div>'
                % (html.escape(c.name), bc, bl)
            )
    else:
        checks_rows = '<div class="stat-row"><span class="stat-label">Not checked yet</span></div>'
    st.markdown(
        '<div class="status-card"><h3>&#128269; System Requirements</h3>%s</div>' % checks_rows,
        unsafe_allow_html=True,
    )

    # Configuration card
    cfg = st.session_state.config
    cfg_rows = (
        '<div class="stat-row"><span class="stat-label">Server IP</span>'
        '<span class="stat-val">%s</span></div>'
        '<div class="stat-row"><span class="stat-label">AE Title</span>'
        '<span class="stat-val">%s</span></div>'
        '<div class="stat-row"><span class="stat-label">Client ID</span>'
        '<span class="stat-val">%s</span></div>'
        '<div class="stat-row"><span class="stat-label">Status</span>'
        '<span class="%s">%s</span></div>'
        % (
            html.escape(cfg.get("ip_address", "") or "—"),
            html.escape(cfg.get("local_ae_title", "") or "—"),
            html.escape(cfg.get("client_id", "") or "—"),
            "badge-pass" if st.session_state.config_ok else "badge-fail",
            "Valid" if st.session_state.config_ok else "Incomplete",
        )
    )
    st.markdown(
        '<div class="status-card"><h3>&#9881; Configuration</h3>%s</div>' % cfg_rows,
        unsafe_allow_html=True,
    )

    # Generated Files card
    files_rows = ""
    if st.session_state.generated_files:
        for line in st.session_state.file_lines:
            files_rows += '<div class="stat-row"><span class="stat-label">%s</span></div>' % line
        files_rows += (
            '<div class="stat-row"><span class="stat-label">Status</span>'
            '<span class="badge-pass">Done</span></div>'
        )
    else:
        files_rows = '<div class="stat-row"><span class="stat-label">Not generated yet</span></div>'
    st.markdown(
        '<div class="status-card"><h3>&#128196; Generated Files</h3>%s</div>' % files_rows,
        unsafe_allow_html=True,
    )

    # Deployment card
    deploy_rows = ""
    for step in dm.state.steps:
        if step.status == "pending":
            continue
        icon, label, _ = format_deploy_line(step)
        deploy_rows += (
            '<div class="stat-row"><span class="stat-label">%s %s</span>'
            '<span class="%s">%s</span></div>'
            % (
                icon, html.escape(label),
                "badge-pass" if step.status == "done" else "badge-fail",
                step.status,
            )
        )
    if not deploy_rows:
        deploy_rows = '<div class="stat-row"><span class="stat-label">Not started yet</span></div>'
    if dm.state.running:
        deploy_rows += '<div class="stat-row"><span class="stat-label">&#128260; Running…</span></div>'
    st.markdown(
        '<div class="status-card"><h3>&#128640; Deployment</h3>%s</div>' % deploy_rows,
        unsafe_allow_html=True,
    )

    st.markdown("</div>", unsafe_allow_html=True)

    all_done = (
        st.session_state.checks_ran
        and overall_checks_passed(st.session_state.checks)
        and st.session_state.config_ok
        and st.session_state.generated_files
        and st.session_state.deploy_done is True
    )
    if all_done:
        ip = st.session_state.config.get("ip_address", "127.0.0.1")
        st.markdown(
            '<div class="status-banner">&#10003; Everything complete — PACS running at http://%s:4000</div>' % ip,
            unsafe_allow_html=True,
        )
    elif st.session_state.deploy_done is False:
        st.markdown(
            '<div class="status-banner fail">&#10007; Deployment failed. Check the Deployment tab for details.</div>',
            unsafe_allow_html=True,
        )


def main() -> None:
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon="\U0001f3e5",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    if st.query_params.get("exit_auto") == "true":
        st.session_state.auto_mode = False
        del st.query_params["exit_auto"]
        st.rerun()

    st.markdown(THEME_CSS, unsafe_allow_html=True)
    init_state()

    render_top_progress()

    left, right = st.columns([1, 3.5])
    with left:
        render_side_tabs()
        if st.session_state.auto_mode:
            st.markdown(
                '<a href="?exit_auto=true" target="_self" '
                'style="color:#ef4444;font-weight:700;text-decoration:none;display:block;'
                'text-align:center;padding:0.45rem;'
                'border:1px solid #ef4444;border-radius:8px;margin-top:0.5rem;">'
                'Exit Automation</a>',
                unsafe_allow_html=True,
            )
    with right:
        step = st.session_state.active_step
        if step == 0:
            panel_system_requirements()
        elif step == 1:
            panel_configuration()
        elif step == 2:
            panel_generate_files()
        elif step == 3:
            panel_deployment()
        elif step == 4:
            panel_status()
        else:
            panel_final_logs()

        if step == 3:
            with st.expander("Activity Logs", expanded=False):
                raw = st.session_state.logger.format_terminal()
                shown = filter_logs(raw)
                if not raw.strip() or raw == "(no logs yet)":
                    st.caption("No activity recorded yet. Run checks or deployment to see logs here.")
                st.code(shown, language="text")

        render_footer(step)

if __name__ == "__main__":
    main()
