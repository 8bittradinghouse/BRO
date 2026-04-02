import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.runtime_hardening_audit import run_audit


class RuntimeHardeningAuditTests(unittest.TestCase):
    def test_hardened_compose_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            compose = root / "docker-compose.yml"
            compose.write_text(
                """
services:
  bro-maker:
    init: true
    restart: unless-stopped
    user: "1001:1001"
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    read_only: true
    tmpfs: ["/tmp:size=64m,noexec,nosuid,nodev"]
    pids_limit: 128
    ulimits:
      nofile:
        soft: 65536
        hard: 65536
      nproc: 4096
    ports: ["127.0.0.1:9108:9108"]
    volumes:
      - ./configs/btc_paper_docker.yaml:/config/config.yaml:ro
      - ./logs_exec:/logs
      - ./data:/data
    environment:
      - PYTHONUNBUFFERED=1
      - PYTHONFAULTHANDLER=1
      - MALLOC_ARENA_MAX=2
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
    healthcheck:
      test: ["CMD", "python", "scripts/container_healthcheck.py", "--config", "/config/config.yaml"]
      interval: 30s
      timeout: 5s
      retries: 3
  bro-guardian:
    init: true
    restart: unless-stopped
    depends_on:
      bro-maker:
        condition: service_healthy
    user: "1001:1001"
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    read_only: true
    tmpfs: ["/tmp:size=32m,noexec,nosuid,nodev"]
    pids_limit: 64
    ulimits:
      nofile:
        soft: 65536
        hard: 65536
      nproc: 4096
    volumes:
      - ./logs_exec:/logs
    environment:
      - PYTHONUNBUFFERED=1
      - PYTHONFAULTHANDLER=1
      - MALLOC_ARENA_MAX=2
    healthcheck:
      test: ["CMD", "python", "scripts/guardian_healthcheck.py", "--log-dir", "/logs/btc_paper"]
      interval: 30s
      timeout: 5s
      retries: 3
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
""",
                encoding="utf-8",
            )
            log_dir = root / "logs"
            data_dir = root / "data"
            with mock.patch("resource.getrlimit", return_value=(65536, 65536)):
                result = run_audit(compose_path=compose, log_dir=log_dir, data_dir=data_dir)
        self.assertTrue(result["ok"])
        self.assertEqual(result["finding_count"], 0)

    def test_config_dir_read_only_mount_is_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            compose = root / "docker-compose.yml"
            compose.write_text(
                """
services:
  bro-maker:
    init: true
    restart: unless-stopped
    user: "1001:1001"
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    read_only: true
    tmpfs: ["/tmp:size=64m,noexec,nosuid,nodev"]
    pids_limit: 128
    ulimits:
      nofile:
        soft: 65536
        hard: 65536
      nproc: 4096
    ports: ["127.0.0.1:9108:9108"]
    volumes:
      - ./configs:/config:ro
      - ./logs_exec:/logs
      - ./data:/data
    environment:
      - PYTHONUNBUFFERED=1
      - PYTHONFAULTHANDLER=1
      - MALLOC_ARENA_MAX=2
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
    healthcheck:
      test: ["CMD", "python", "scripts/container_healthcheck.py", "--config", "/config/profiles/paper_universal.yaml"]
      interval: 30s
      timeout: 5s
      retries: 3
  bro-guardian:
    init: true
    restart: unless-stopped
    depends_on:
      bro-maker:
        condition: service_healthy
    user: "1001:1001"
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    read_only: true
    tmpfs: ["/tmp:size=32m,noexec,nosuid,nodev"]
    pids_limit: 64
    ulimits:
      nofile:
        soft: 65536
        hard: 65536
      nproc: 4096
    volumes:
      - ./logs_exec:/logs
    environment:
      - PYTHONUNBUFFERED=1
      - PYTHONFAULTHANDLER=1
      - MALLOC_ARENA_MAX=2
    healthcheck:
      test: ["CMD", "python", "scripts/guardian_healthcheck.py", "--log-dir", "/logs/btc_paper"]
      interval: 30s
      timeout: 5s
      retries: 3
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
""",
                encoding="utf-8",
            )
            with mock.patch("resource.getrlimit", return_value=(65536, 65536)):
                result = run_audit(compose_path=compose, log_dir=root / "logs", data_dir=root / "data")
        self.assertTrue(result["ok"], msg=str(result["findings"]))

    def test_non_local_metrics_bind_is_finding(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            compose = root / "docker-compose.yml"
            compose.write_text(
                """
services:
  bro-maker:
    init: true
    restart: unless-stopped
    user: "1001:1001"
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    read_only: true
    tmpfs: ["/tmp:size=64m,noexec,nosuid,nodev"]
    pids_limit: 128
    ulimits:
      nofile:
        soft: 65536
        hard: 65536
      nproc: 4096
    ports: ["0.0.0.0:9108:9108"]
    volumes:
      - ./configs/btc_paper_docker.yaml:/config/config.yaml:ro
      - ./logs_exec:/logs
      - ./data:/data
    environment:
      - PYTHONUNBUFFERED=1
      - PYTHONFAULTHANDLER=1
      - MALLOC_ARENA_MAX=2
    healthcheck:
      test: ["CMD", "python", "scripts/container_healthcheck.py", "--config", "/config/config.yaml"]
      interval: 30s
      timeout: 5s
      retries: 3
  bro-guardian:
    init: true
    restart: unless-stopped
    depends_on:
      bro-maker:
        condition: service_healthy
    user: "1001:1001"
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    read_only: true
    tmpfs: ["/tmp:size=32m,noexec,nosuid,nodev"]
    pids_limit: 64
    ulimits:
      nofile:
        soft: 65536
        hard: 65536
      nproc: 4096
    volumes:
      - ./logs_exec:/logs
    environment:
      - PYTHONUNBUFFERED=1
      - PYTHONFAULTHANDLER=1
      - MALLOC_ARENA_MAX=2
    healthcheck:
      test: ["CMD", "python", "scripts/guardian_healthcheck.py", "--log-dir", "/logs/btc_paper"]
      interval: 30s
      timeout: 5s
      retries: 3
""",
                encoding="utf-8",
            )
            log_dir = root / "logs"
            data_dir = root / "data"
            with mock.patch("resource.getrlimit", return_value=(65536, 65536)):
                result = run_audit(compose_path=compose, log_dir=log_dir, data_dir=data_dir)
        self.assertFalse(result["ok"])
        self.assertTrue(any("runtime_service_metrics_bind_not_localhost" in f for f in result["findings"]))

    def test_root_user_is_finding(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            compose = root / "docker-compose.yml"
            compose.write_text(
                """
services:
  bro-maker:
    init: true
    restart: unless-stopped
    user: "0:0"
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    read_only: true
    tmpfs: ["/tmp:size=64m,noexec,nosuid,nodev"]
    pids_limit: 128
    ulimits:
      nofile: {soft: 65536, hard: 65536}
      nproc: 4096
    ports: ["127.0.0.1:9108:9108"]
    volumes:
      - ./configs/btc_paper_docker.yaml:/config/config.yaml:ro
      - ./logs_exec:/logs
      - ./data:/data
    environment:
      - PYTHONUNBUFFERED=1
      - PYTHONFAULTHANDLER=1
      - MALLOC_ARENA_MAX=2
    healthcheck:
      test: ["CMD", "python", "scripts/container_healthcheck.py", "--config", "/config/config.yaml"]
      interval: 30s
      timeout: 5s
      retries: 3
  bro-guardian:
    init: true
    restart: unless-stopped
    depends_on:
      bro-maker:
        condition: service_healthy
    user: "1001:1001"
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    read_only: true
    tmpfs: ["/tmp:size=32m,noexec,nosuid,nodev"]
    pids_limit: 64
    ulimits:
      nofile: {soft: 65536, hard: 65536}
      nproc: 4096
    volumes:
      - ./logs_exec:/logs
    environment:
      - PYTHONUNBUFFERED=1
      - PYTHONFAULTHANDLER=1
      - MALLOC_ARENA_MAX=2
    healthcheck:
      test: ["CMD", "python", "scripts/guardian_healthcheck.py", "--log-dir", "/logs/btc_paper"]
      interval: 30s
      timeout: 5s
      retries: 3
""",
                encoding="utf-8",
            )
            with mock.patch("resource.getrlimit", return_value=(65536, 65536)):
                result = run_audit(compose_path=compose, log_dir=root / "logs", data_dir=root / "data")
        self.assertFalse(result["ok"])
        self.assertTrue(any("runtime_service_user_is_root" in f for f in result["findings"]))

    def test_missing_restart_policy_is_finding(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            compose = root / "docker-compose.yml"
            compose.write_text(
                """
services:
  bro-maker:
    init: true
    user: "1001:1001"
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    read_only: true
    tmpfs: ["/tmp:size=64m,noexec,nosuid,nodev"]
    pids_limit: 128
    ulimits:
      nofile: {soft: 65536, hard: 65536}
      nproc: 4096
    ports: ["127.0.0.1:9108:9108"]
    volumes:
      - ./configs/btc_paper_docker.yaml:/config/config.yaml:ro
      - ./logs_exec:/logs
      - ./data:/data
    environment:
      - PYTHONUNBUFFERED=1
      - PYTHONFAULTHANDLER=1
      - MALLOC_ARENA_MAX=2
    healthcheck:
      test: ["CMD", "python", "scripts/container_healthcheck.py", "--config", "/config/config.yaml"]
      interval: 30s
      timeout: 5s
      retries: 3
  bro-guardian:
    init: true
    restart: unless-stopped
    depends_on:
      bro-maker:
        condition: service_healthy
    user: "1001:1001"
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    read_only: true
    tmpfs: ["/tmp:size=32m,noexec,nosuid,nodev"]
    pids_limit: 64
    ulimits:
      nofile: {soft: 65536, hard: 65536}
      nproc: 4096
    volumes:
      - ./logs_exec:/logs
    environment:
      - PYTHONUNBUFFERED=1
      - PYTHONFAULTHANDLER=1
      - MALLOC_ARENA_MAX=2
    healthcheck:
      test: ["CMD", "python", "scripts/guardian_healthcheck.py", "--log-dir", "/logs/btc_paper"]
      interval: 30s
      timeout: 5s
      retries: 3
""",
                encoding="utf-8",
            )
            with mock.patch("resource.getrlimit", return_value=(65536, 65536)):
                result = run_audit(compose_path=compose, log_dir=root / "logs", data_dir=root / "data")
        self.assertFalse(result["ok"])
        self.assertTrue(any("runtime_service_restart_policy_invalid:bro-maker" in f for f in result["findings"]))

    def test_guardian_missing_depends_on_health_is_finding(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            compose = root / "docker-compose.yml"
            compose.write_text(
                """
services:
  bro-maker:
    init: true
    restart: unless-stopped
    user: "1001:1001"
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    read_only: true
    tmpfs: ["/tmp:size=64m,noexec,nosuid,nodev"]
    pids_limit: 128
    ulimits:
      nofile: {soft: 65536, hard: 65536}
      nproc: 4096
    ports: ["127.0.0.1:9108:9108"]
    volumes:
      - ./configs/btc_paper_docker.yaml:/config/config.yaml:ro
      - ./logs_exec:/logs
      - ./data:/data
    environment:
      - PYTHONUNBUFFERED=1
      - PYTHONFAULTHANDLER=1
      - MALLOC_ARENA_MAX=2
    healthcheck:
      test: ["CMD", "python", "scripts/container_healthcheck.py", "--config", "/config/config.yaml"]
      interval: 30s
      timeout: 5s
      retries: 3
  bro-guardian:
    init: true
    restart: unless-stopped
    user: "1001:1001"
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    read_only: true
    tmpfs: ["/tmp:size=32m,noexec,nosuid,nodev"]
    pids_limit: 64
    ulimits:
      nofile: {soft: 65536, hard: 65536}
      nproc: 4096
    volumes:
      - ./logs_exec:/logs
    environment:
      - PYTHONUNBUFFERED=1
      - PYTHONFAULTHANDLER=1
      - MALLOC_ARENA_MAX=2
    healthcheck:
      test: ["CMD", "python", "scripts/guardian_healthcheck.py", "--log-dir", "/logs/btc_paper"]
      interval: 30s
      timeout: 5s
      retries: 3
""",
                encoding="utf-8",
            )
            with mock.patch("resource.getrlimit", return_value=(65536, 65536)):
                result = run_audit(compose_path=compose, log_dir=root / "logs", data_dir=root / "data")
        self.assertFalse(result["ok"])
        self.assertTrue(any("runtime_service_depends_on_missing:bro-guardian" in f for f in result["findings"]))


if __name__ == "__main__":
    unittest.main()
