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
    assert "mem_limit: 512m" in _service(overlay, "worker")
    for name, command in (
        ("scheduler", "scheduler"),
        ("agent", "agent"),
        ("sourced-handoff", "sourced-handoff"),
    ):
        worker = _service(overlay, name)
        assert "restart: unless-stopped" in worker
        assert f'command: ["cresta-worker", "{command}"]' in worker
        expected_memory = "512m" if name == "agent" else "256m"
        assert f"mem_limit: {expected_memory}" in worker
        assert "cpus: 0.25" in worker
    assert "restart: on-failure" not in base


def test_one_shot_migration_is_the_only_runtime_startup_gate() -> None:
    base = (ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8")
    overlay = (ROOT / "deploy" / "compose.kiwoom.yaml").read_text(encoding="utf-8")
    migration = _service(base, "migration")
    assert 'restart: "no"' in migration
    assert 'command: ["alembic", "upgrade", "head"]' in migration
    assert "postgres:\n        condition: service_healthy" in migration
    assert "service_completed_successfully" in _service(base, "api")
    for name in ("worker", "scheduler", "agent", "sourced-handoff"):
        assert "migration:\n        condition: service_completed_successfully" in _service(
            overlay, name
        )
        assert "alembic" not in _service(overlay, name)
    assert "alembic" not in _service(base, "api")


def test_user_facing_services_have_health_gated_startup() -> None:
    config = (ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8")

    for name in ("postgres", "redis", "api", "frontend", "nginx"):
        assert "healthcheck:" in _service(config, name)

    frontend = _service(config, "frontend")
    gateway = _service(config, "nginx")
    assert "api:\n        condition: service_healthy" in frontend
    assert "api:\n        condition: service_healthy" in gateway
    assert "frontend:\n        condition: service_healthy" in gateway
    assert "/readyz" in _service(config, "api")


def test_compose_has_bounded_logs_and_internal_database_ports() -> None:
    base = (ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8")
    overlay = (ROOT / "deploy" / "compose.kiwoom.yaml").read_text(encoding="utf-8")
    assert 'max-size: "10m"' in base and 'max-file: "5"' in base
    assert 'max-size: "10m"' in overlay and 'max-file: "5"' in overlay
    for name in ("postgres", "redis", "migration", "api", "frontend", "nginx"):
        assert "logging: *default-logging" in _service(base, name)
    for name in ("worker", "scheduler", "agent", "sourced-handoff"):
        assert "logging: *default-logging" in _service(overlay, name)
    assert "ports:" not in _service(base, "postgres")
    assert "ports:" not in _service(base, "redis")
    assert '"127.0.0.1:7788:8080"' in _service(base, "nginx")


def test_boot_unit_uses_optional_source_overlay_reconciler() -> None:
    unit = (ROOT / "deploy" / "cresta-boot.service").read_text(encoding="utf-8")
    script = (ROOT / "deploy" / "boot-reconcile.sh").read_text(encoding="utf-8")

    assert "After=network-online.target docker.service" in unit
    assert "Requires=docker.service" in unit
    assert "WorkingDirectory=/home/totquf4171/cresta" in unit
    assert "boot-reconcile.sh --check" in unit
    assert "boot-reconcile.sh --up" in unit
    assert "-f deploy/compose.yaml -f deploy/compose.kiwoom.yaml" in script
    assert "[ -s secrets/dart_api_key ]" in script
    assert "-f deploy/compose.dart.yaml" in script
    assert "[ -s secrets/krx_api_key ]" in script
    assert "-f deploy/compose.krx.yaml" in script
    assert "naver_api_hub_client_id" in script
    assert "naver_api_hub_client_secret" in script
    assert "-f deploy/compose.naver-news.yaml" in script
    assert "config --quiet" in script
    assert "up -d --wait --wait-timeout 180" in script
    assert "Restart=on-failure" in unit
    assert "StartLimitBurst=5" in unit
