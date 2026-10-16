"""No live load balancing claims: safe config and port admission tests."""
from pathlib import Path

import pytest

from app.local_inference import LocalGatewayChat, selected_local_gateway_port


CONFIG = Path(__file__).resolve().parents[3] / "infrastructure/load-balancer/haproxy.local.cfg"


def test_only_preapproved_loopback_ports_can_be_selected():
    assert selected_local_gateway_port({}) == 8081
    assert selected_local_gateway_port({"METRIQEN_LOCAL_CHAT_GATEWAY_PORT": "8090"}) == 8090
    for illegal in ("", "8091", "0", "443", "http://evil.test:8090", " 8090",
                    "127.0.0.1:8090", "8082", "9090", "-1"):
        with pytest.raises(ValueError):
            selected_local_gateway_port({"METRIQEN_LOCAL_CHAT_GATEWAY_PORT": illegal})
    assert LocalGatewayChat(port=8090).ORIGIN == "http://127.0.0.1:8090"
    assert LocalGatewayChat().ORIGIN == "http://127.0.0.1:8081"
    for illegal in ("8090", True, -1, 443, 8082):
        with pytest.raises(ValueError):
            LocalGatewayChat(port=illegal)


def test_haproxy_config_limits_surface_to_local_chat_and_healthchecked_replicas():
    config = CONFIG.read_text(encoding="utf-8")
    assert "bind 127.0.0.1:8090" in config
    assert "balance leastconn" in config
    assert "option httpchk GET /health/upstream" in config
    assert "http-check expect status 200" in config
    assert "http-request deny deny_status 404 unless chat_endpoint chat_method" in config
    assert "acl chat_method method POST" in config
    assert "acl chat_endpoint path -i /v1/chat/completions" in config
    assert "server gateway_one 127.0.0.1:8081" in config
    assert "server gateway_two 127.0.0.1:8082" in config
    assert "timeout server 180s" in config
    assert "bind 0.0.0.0" not in config
    assert "stats enable" not in config
    assert "option httpclose" not in config
