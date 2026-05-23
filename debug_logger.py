from __future__ import annotations

import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from backend.models import CommandLogEntry


class DebugLogger:
    """Structured command logging for the terminal panel and debug reports."""

    def __init__(self) -> None:
        self._entries: list[CommandLogEntry] = []
        self._lock = threading.Lock()
        self._listeners: list[Callable[[], None]] = []

    @property
    def entries(self) -> list[CommandLogEntry]:
        with self._lock:
            return list(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def on_update(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def _notify(self) -> None:
        for callback in self._listeners:
            try:
                callback()
            except Exception:
                pass

    def run(
        self,
        step_name: str,
        command: list[str],
        cwd: Path | str,
        *,
        stream: bool = True,
        timeout: int | None = None,
        user_message: str = "",
        technical_error: str = "",
        suggested_fix: str = "",
    ) -> CommandLogEntry:
        cwd_str = str(Path(cwd))
        entry = CommandLogEntry(
            step_name=step_name,
            command=" ".join(command),
            working_directory=cwd_str,
            started_at=datetime.now(),
        )
        try:
            if stream:
                proc = subprocess.Popen(
                    command,
                    cwd=cwd_str,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
                stdout_parts: list[str] = []
                stderr_parts: list[str] = []

                def read_stream(pipe: subprocess.PIPE | None, parts: list[str]) -> None:
                    if pipe:
                        for line in pipe:
                            parts.append(line)

                thread_out = threading.Thread(
                    target=read_stream, args=(proc.stdout, stdout_parts), daemon=True
                )
                thread_err = threading.Thread(
                    target=read_stream, args=(proc.stderr, stderr_parts), daemon=True
                )
                thread_out.start()
                thread_err.start()
                proc.wait(timeout=timeout)
                thread_out.join(timeout=5)
                thread_err.join(timeout=5)
                entry.stdout = "".join(stdout_parts)
                entry.stderr = "".join(stderr_parts)
                entry.exit_code = proc.returncode
            else:
                result = subprocess.run(
                    command,
                    cwd=cwd_str,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                entry.stdout = result.stdout or ""
                entry.stderr = result.stderr or ""
                entry.exit_code = result.returncode

            entry.success = entry.exit_code == 0
            if not user_message:
                entry.user_message = (
                    f"{step_name} completed successfully."
                    if entry.success
                    else f"{step_name} failed."
                )
            else:
                entry.user_message = user_message

            combined = f"{entry.stderr}\n{entry.stdout}".strip()
            entry.technical_error = technical_error or (combined if not entry.success else "")
            entry.suggested_fix = suggested_fix
        except FileNotFoundError as exc:
            entry.exit_code = -1
            entry.success = False
            entry.technical_error = str(exc)
            entry.user_message = f"Command not found: {command[0]}"
            entry.suggested_fix = suggested_fix or f"Ensure '{command[0]}' is installed and in PATH."
        except subprocess.TimeoutExpired as exc:
            entry.exit_code = -1
            entry.success = False
            entry.technical_error = str(exc)
            entry.user_message = f"{step_name} timed out after {timeout}s."
        except Exception as exc:
            entry.exit_code = -1
            entry.success = False
            entry.technical_error = str(exc)
            entry.user_message = f"{step_name} error: {exc}"
        finally:
            entry.ended_at = datetime.now()
            with self._lock:
                self._entries.append(entry)
            self._notify()
        return entry

    def format_terminal(self) -> str:
        lines: list[str] = []
        for entry in self.entries:
            lines.append(f"[{entry.started_at:%Y-%m-%d %H:%M:%S}] === {entry.step_name} ===")
            lines.append(f"$ {entry.command}")
            lines.append(f"cwd: {entry.working_directory}")
            if entry.stdout:
                lines.append("--- stdout ---")
                lines.append(entry.stdout.rstrip())
            if entry.stderr:
                lines.append("--- stderr ---")
                lines.append(entry.stderr.rstrip())
            duration = entry.duration_seconds()
            dur = f" ({duration:.1f}s)" if duration is not None else ""
            lines.append(
                f"exit={entry.exit_code} success={entry.success}{dur}"
            )
            if entry.user_message:
                lines.append(f"→ {entry.user_message}")
            if entry.technical_error and not entry.success:
                lines.append(f"Technical: {entry.technical_error[:2000]}")
            if entry.suggested_fix:
                lines.append(f"Suggested fix: {entry.suggested_fix}")
            lines.append("")
        return "\n".join(lines)
