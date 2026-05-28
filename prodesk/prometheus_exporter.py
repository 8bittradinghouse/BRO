from __future__ import annotations

from typing import Any, Dict, Optional

from .paths import docker_mode_enabled

try:
    from prometheus_client import CollectorRegistry, Gauge, start_http_server
except ImportError:  # pragma: no cover
    CollectorRegistry = None  # type: ignore[assignment]
    Gauge = None  # type: ignore[assignment]
    start_http_server = None  # type: ignore[assignment]


class PrometheusExporterError(RuntimeError):
    pass


class PrometheusExporter:
    def __init__(self, cfg: Dict[str, Any]):
        self.enabled = bool(cfg.get("enabled", False))
        self.host = str(cfg.get("host", "0.0.0.0"))
        self.port = int(cfg.get("port", 9108))
        self.namespace = str(cfg.get("namespace", "prodesk")).strip().lower() or "prodesk"
        self._server: Optional[Any] = None
        self._server_thread: Optional[Any] = None
        self._registry: Optional[Any] = None
        self._metric_value = None
        self._status_value = None
        self._is_started = False
        self._effective_host = self.host

    def _resolve_bind_host(self) -> str:
        host = str(self.host or "").strip() or "0.0.0.0"
        if docker_mode_enabled() and host.lower() in {"127.0.0.1", "localhost"}:
            return "0.0.0.0"
        return host

    def start(self) -> None:
        if not self.enabled:
            return
        if CollectorRegistry is None or Gauge is None or start_http_server is None:
            raise PrometheusExporterError(
                "metrics.enabled is true but prometheus-client is missing. Install with `pip install prometheus-client`."
            )
        if self._is_started:
            return
        self._registry = CollectorRegistry()
        self._effective_host = self._resolve_bind_host()
        try:
            self._metric_value = Gauge(
                f"{self.namespace}_metric_value",
                "Dynamic metric values exported from bot telemetry",
                ["metric"],
                registry=self._registry,
            )
            self._status_value = Gauge(
                f"{self.namespace}_status_value",
                "Execution status values",
                ["name"],
                registry=self._registry,
            )
            server_result = start_http_server(port=self.port, addr=self._effective_host, registry=self._registry)
            if isinstance(server_result, tuple):
                self._server = server_result[0] if len(server_result) >= 1 else None
                self._server_thread = server_result[1] if len(server_result) >= 2 else None
            else:
                self._server = server_result
                self._server_thread = None
        except (OSError, RuntimeError, ValueError) as exc:
            raise PrometheusExporterError(
                f"failed to start metrics server on {self._effective_host}:{self.port}: {exc}"
            ) from exc
        self._is_started = True

    def update(self, snapshot: Dict[str, float], status_values: Dict[str, float]) -> None:
        if not self.enabled or not self._is_started:
            return
        assert self._metric_value is not None
        assert self._status_value is not None
        for key, value in snapshot.items():
            if isinstance(value, (int, float)):
                self._metric_value.labels(metric=key).set(float(value))
        for key, value in status_values.items():
            if isinstance(value, (int, float)):
                self._status_value.labels(name=key).set(float(value))

    def stop(self) -> None:
        if not self.enabled:
            return
        if self._server is not None and hasattr(self._server, "shutdown"):
            try:
                self._server.shutdown()
            except (OSError, RuntimeError):
                pass
        self._server = None
        self._server_thread = None
        self._registry = None
        self._is_started = False
