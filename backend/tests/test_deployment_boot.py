import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _service(config: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-z][a-z0-9_-]*:\n|^[a-z][a-z0-9_-]*:\n|\Z)",
        config,
    )
    assert match is not None, f"service {name!r} not found"
    return match.group(1)


def test_long_running_services_restart_after_docker_daemon_restart() -> None:
    base = (ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8")
    overlay = (ROOT / "deploy" / "compose.kiwoom.yaml").read_text(encoding="utf-8")

    for name in ("postgres", "redis", "api", "frontend", "nginx"):
        assert "restart: unless-stopped" in _service(base, name)
    assert "restart: unless-stopped" in _service(overlay, "worker")
    scheduler = _service(overlay, "scheduler")
    assert "restart: unless-stopped" in scheduler
    assert 'command: ["cresta-worker", "scheduler"]' in scheduler
    assert "mem_limit: 256m" in scheduler
    assert "cpus: 0.25" in scheduler
    assert "restart: on-failure" not in base


def test_user_facing_services_have_health_gated_startup() -> None:
    config = (ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8")

    for name in ("postgres", "redis", "api", "frontend", "nginx"):
        assert "healthcheck:" in _service(config, name)

    frontend = _service(config, "frontend")
    gateway = _service(config, "nginx")
    assert "api:\n        condition: service_healthy" in frontend
    assert "api:\n        condition: service_healthy" in gateway
    assert "frontend:\n        condition: service_healthy" in gateway


def test_boot_unit_reconciles_both_compose_files_and_waits_for_health() -> None:
    unit = (ROOT / "deploy" / "cresta-boot.service").read_text(encoding="utf-8")

    assert "After=network-online.target docker.service" in unit
    assert "Requires=docker.service" in unit
    assert "WorkingDirectory=/home/totquf4171/cresta" in unit
    assert unit.count("-f deploy/compose.yaml -f deploy/compose.kiwoom.yaml") == 2
    assert "config --quiet" in unit
    assert "up -d --wait --wait-timeout 180" in unit
    assert "Restart=on-failure" in unit
    assert "StartLimitBurst=5" in unit
