from pathlib import Path


def test_gateway_uses_docker_dns_for_recreated_upstreams() -> None:
    config = (
        Path(__file__).resolve().parents[2] / "deploy" / "nginx.conf"
    ).read_text(encoding="utf-8")

    assert "resolver 127.0.0.11 valid=10s ipv6=off;" in config
    assert "set $api_upstream api:8000;" in config
    assert "set $frontend_upstream frontend:3000;" in config
    assert "proxy_pass http://$api_upstream;" in config
    assert "proxy_pass http://$api_upstream/healthz;" in config
    assert "proxy_pass http://$frontend_upstream;" in config
    assert "proxy_pass http://api:8000" not in config
    assert "proxy_pass http://frontend:3000" not in config
