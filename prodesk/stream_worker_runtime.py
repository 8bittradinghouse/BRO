from __future__ import annotations

import collections
import json
import os
import pathlib
import queue
import shutil
import subprocess
import threading
import time
from typing import Any, Deque, Dict, List, Optional


class StreamWorkerError(RuntimeError):
    pass


def project_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[1]


def resolve_worker_command(
    *,
    worker_name: str,
    config_path_value: Any,
    env_var: str,
) -> List[str]:
    raw = str(config_path_value or "").strip()
    if not raw:
        raw = str(os.getenv(env_var, "")).strip()
    candidates: List[pathlib.Path] = []
    if raw:
        candidates.append(pathlib.Path(raw).expanduser().resolve())
    root = project_root()
    candidates.extend(
        [
            root / "workers" / "bin" / worker_name,
            root / "workers" / "target" / "release" / worker_name,
        ]
    )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return [str(candidate)]
    resolved = shutil.which(worker_name)
    if resolved:
        return [resolved]
    raise StreamWorkerError(
        f"worker binary not found for {worker_name}; checked config/env {env_var!r}, "
        "workers/bin, workers/target/release, and PATH"
    )


class StdioJsonWorkerProcess:
    def __init__(
        self,
        *,
        command: List[str],
        name: str,
        stderr_tail_lines: int = 100,
        stdout_queue_max: int = 2048,
    ) -> None:
        self._command = list(command)
        self._name = str(name)
        self._stderr_tail: Deque[str] = collections.deque(maxlen=max(1, int(stderr_tail_lines)))
        self._stdout_queue_max = max(8, int(stdout_queue_max))
        self._stdout_queue: queue.Queue[Dict[str, Any]] = queue.Queue(maxsize=self._stdout_queue_max)
        self._stdin_lock = threading.Lock()
        self._process: Optional[subprocess.Popen[str]] = None
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._terminal_event_sent = False

    def start(self) -> None:
        if self._process is not None:
            raise StreamWorkerError(f"{self._name} already started")
        try:
            self._process = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as exc:
            raise StreamWorkerError(f"failed to start {self._name}: {exc}") from exc
        if self._process.stdin is None or self._process.stdout is None or self._process.stderr is None:
            raise StreamWorkerError(f"failed to create stdio pipes for {self._name}")
        self._stdout_thread = threading.Thread(
            target=self._stdout_loop,
            name=f"{self._name}-stdout",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._stderr_loop,
            name=f"{self._name}-stderr",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def send(self, payload: Dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise StreamWorkerError(f"{self._name} is not started")
        line = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        with self._stdin_lock:
            try:
                process.stdin.write(line + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise StreamWorkerError(f"{self._name} stdin write failed: {exc}") from exc

    def recv(self, timeout: float) -> Optional[Dict[str, Any]]:
        try:
            return self._stdout_queue.get(timeout=max(0.0, float(timeout)))
        except queue.Empty:
            return None

    def poll(self) -> Optional[int]:
        return None if self._process is None else self._process.poll()

    def stderr_tail(self) -> List[str]:
        return list(self._stderr_tail)

    def terminate(self, *, timeout_sec: float = 5.0) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.stdin is not None:
                with self._stdin_lock:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=max(0.1, float(timeout_sec)))
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=max(0.1, float(timeout_sec)))
        finally:
            if self._stdout_thread is not None:
                self._stdout_thread.join(timeout=max(0.1, float(timeout_sec)))
            if self._stderr_thread is not None:
                self._stderr_thread.join(timeout=max(0.1, float(timeout_sec)))

    def _stdout_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                now_mono = time.monotonic()
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    self._publish_terminal_event(
                        payload={
                            "event": "fatal",
                            "fatal_reason": "decode_error",
                            "usable": False,
                            "raw": line,
                        },
                        received_monotonic=now_mono,
                    )
                    return
                item = {
                    "payload": payload,
                    "received_monotonic": now_mono,
                }
                try:
                    self._stdout_queue.put_nowait(item)
                except queue.Full:
                    self._publish_terminal_event(
                        payload={
                            "event": "fatal",
                            "fatal_reason": "queue_overflow",
                            "usable": False,
                            "dropped_pending_events": int(self._stdout_queue.qsize()) + 1,
                        },
                        received_monotonic=now_mono,
                    )
                    return
        finally:
            self._publish_terminal_event(
                payload={"event": "worker_eof", "usable": False},
                received_monotonic=time.monotonic(),
            )

    def _stderr_loop(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for raw_line in process.stderr:
            line = raw_line.rstrip()
            if not line:
                continue
            self._stderr_tail.append(line)

    def _publish_terminal_event(self, *, payload: Dict[str, Any], received_monotonic: float) -> None:
        if self._terminal_event_sent:
            return
        self._terminal_event_sent = True
        item = {
            "payload": dict(payload),
            "received_monotonic": float(received_monotonic),
        }
        try:
            self._stdout_queue.put_nowait(item)
            return
        except queue.Full:
            pass
        while True:
            try:
                self._stdout_queue.get_nowait()
            except queue.Empty:
                break
        try:
            self._stdout_queue.put_nowait(item)
        except queue.Full:
            pass
