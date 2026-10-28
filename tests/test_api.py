import time

from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.cache import ExactCache
from gateway.core import Gateway
from gateway.providers import MockProvider
from gateway.resilience import TokenBucket


def client(limiter=None, workers=2):
    emb_gw = Gateway({"small": [MockProvider("s", 0.01)], "large": [MockProvider("l", 0.01)]}, None, ExactCache(), None)
    return TestClient(create_app(emb_gw, {"k1": "A", "k2": "B"}, limiter, workers))


H = {"x-api-key": "k1"}


def test_auth_validation_and_chat_roundtrip():
    with client() as c:
        assert c.post("/v1/chat", json={"prompt": "oi"}).status_code == 401
        assert c.post("/v1/chat", json={"prompt": ""}, headers=H).status_code == 422
        assert c.post("/v1/chat", json={"prompt": "oi", "model_hint": "huge"}, headers=H).status_code == 422
        r1 = c.post("/v1/chat", json={"prompt": "oi tudo bem?"}, headers=H).json()
        r2 = c.post("/v1/chat", json={"prompt": "oi tudo bem?"}, headers=H).json()
        assert r1["cache"]["layer"] == "none" and r2["cache"]["layer"] == "exact" and r2["usage"]["cost_usd"] == 0


def test_rate_limit_returns_429_with_retry_after():
    with client(TokenBucket(rate=0.1, burst=1)) as c:
        assert c.post("/v1/chat", json={"prompt": "a"}, headers=H).status_code == 200
        r = c.post("/v1/chat", json={"prompt": "b"}, headers=H)
        assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


def wait(c, jid, headers):
    for _ in range(100):
        j = c.get(f"/v1/jobs/{jid}", headers=headers).json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.02)
    raise AssertionError("job timeout")


def test_async_job_idempotency_and_tenant_isolation():
    with client() as c:
        h = {**H, "idempotency-key": "abc"}
        a = c.post("/v1/jobs", json={"prompt": "resuma"}, headers=h)
        b = c.post("/v1/jobs", json={"prompt": "resuma"}, headers=h)
        assert a.status_code == 202 and a.json()["job_id"] == b.json()["job_id"]
        done = wait(c, a.json()["job_id"], H)
        assert done["status"] == "done" and done["result"]["text"]
        assert c.get(f"/v1/jobs/{a.json()['job_id']}", headers={"x-api-key": "k2"}).status_code == 404


def test_metrics_endpoint_exposes_prometheus_text():
    with client() as c:
        c.post("/v1/chat", json={"prompt": "oi"}, headers=H)
        body = c.get("/metrics").text
        assert 'gateway_requests_total{cache="none",tier="small"} 1.0' in body
        assert "gateway_latency_seconds_bucket" in body and 'gateway_cost_usd_total{tenant="A"}' in body
