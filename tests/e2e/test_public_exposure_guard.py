"""Fail-closed offline regression checks for the *development* network edge.

These checks deliberately do not attest deployed HTTPS, Google OAuth or GPU
tenancy. They only prevent accidental publication of current local services.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]


class LocalServiceExposureGuard(unittest.TestCase):
    def test_docker_compose_host_ports_remain_loopback_only(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        # This development-only file has exactly two quoted host port maps.
        ports = re.findall(r'^\s*-\s*"([^"]+)"\s*$', compose, flags=re.MULTILINE)
        self.assertEqual(ports, ["127.0.0.1:8000:8000",
                                 "127.0.0.1:8081:8081"])
        self.assertIn("Local development only", compose)
        self.assertNotIn("127.0.0.1:8000:8000/udp", compose)

    def test_go_inference_rejects_public_listener_in_runtime(self):
        gateway = (ROOT / "services/inference-gateway/cmd/server/main.go").read_text(
            encoding="utf-8")
        for guard in (
            'addr = "127.0.0.1:8081"',
            "if !listenerAllowed(addr, upstream)",
            'log.Fatal("local inference must bind to loopback only")',
            'return hostname == "127.0.0.1" || hostname == "localhost" || hostname == "::1"',
            "router = secureGatewayHandler(addr, upstream, router)",
        ):
            self.assertIn(guard, gateway)

    def test_production_unsafe_fastapi_bff_never_loses_local_host_guard(self):
        entrypoint = (ROOT / "services/control-api/app/main.py").read_text(
            encoding="utf-8")
        browser = (ROOT / "services/control-api/app/browser_auth.py").read_text(
            encoding="utf-8")
        self.assertIn("enforce_local_http_host=True", entrypoint)
        self.assertIn('LOCAL_ORIGIN = "http://127.0.0.1:8000"', browser)
        self.assertIn('SESSION_COOKIE = "metriqen_dev_session"', browser)
        self.assertIn("secure=False, samesite=\"lax\"", browser)
        self.assertIn("browser sign-in is local-only", browser)
        # These assertions are deployment BLOCKERS, not desired production
        # session settings. A future production service must be separate.
        security_guide = (ROOT / "docs/security/public-deployment-boundaries.md").read_text(
            encoding="utf-8")
        self.assertIn("BLOCKED for public production ingress", security_guide)
        self.assertIn("independently hardened production entrypoint", security_guide)
        self.assertIn("not a GPU scale action", security_guide)

    def test_local_launcher_does_not_serve_cloud_reviewer(self):
        launcher = (ROOT / "scripts/start-local-demo.sh").read_text(encoding="utf-8")
        self.assertIn("127.0.0.1:8000", launcher)
        self.assertIn("127.0.0.1:8081", launcher)
        self.assertIn('[[ "$(uname -s)" == "Darwin" ]]', launcher)
        self.assertNotIn("0.0.0.0:8081", launcher)
        self.assertNotIn("0.0.0.0:8000", launcher)


if __name__ == "__main__":
    unittest.main()
