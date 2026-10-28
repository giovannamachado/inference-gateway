import os
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

from .cache import ExactCache, SemanticCache
from .core import AllProvidersFailed, Gateway
from .embed import HashingEmbedder
from .jobs import JobManager, QueueFull
from .providers import MockProvider
from .resilience import TokenBucket
from .verify import lexical_equivalent


class ChatIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    model_hint: Literal["auto", "small", "large"] = "auto"
    max_tokens: int = Field(256, ge=1, le=4096)


def build_gateway() -> tuple[Gateway, dict]:
    """Configuração por variáveis de ambiente (usado pelos cenários de benchmark)."""
    mode = os.getenv("GATEWAY_CACHE", "semantic")  # none | exact | semantic
    emb = HashingEmbedder()
    exact = ExactCache() if mode in ("exact", "semantic") else None
    verifier = lexical_equivalent if os.getenv("SEMANTIC_VERIFY", "1") == "1" else None
    sem = SemanticCache(emb, threshold=float(os.getenv("SEMANTIC_THRESHOLD", "0.8")), verifier=verifier) if mode == "semantic" else None
    lat_s, lat_l = float(os.getenv("MOCK_LATENCY_SMALL", "0.15")), float(os.getenv("MOCK_LATENCY_LARGE", "0.6"))
    fail = float(os.getenv("MOCK_FAIL_RATE", "0"))
    providers = {"small": [MockProvider("small-a", lat_s, fail_rate=fail, seed=1), MockProvider("small-b", lat_s, seed=2)],
                 "large": [MockProvider("large-a", lat_l, fail_rate=fail, seed=3), MockProvider("large-b", lat_l, seed=4)]}
    gw = Gateway(providers, emb, exact, sem)
    keys = dict(kv.split(":") for kv in os.getenv("GATEWAY_API_KEYS", "dev-key:dev").split(","))
    return gw, keys


def create_app(gateway: Gateway | None = None, api_keys: dict | None = None, limiter: TokenBucket | None = None, workers: int = 4) -> FastAPI:
    gw, env_keys = (gateway, {"dev-key": "dev"}) if gateway else build_gateway()
    keys = api_keys or env_keys
    limiter = limiter or TokenBucket(rate=float(os.getenv("RATE_LIMIT_RPS", "1000")), burst=float(os.getenv("RATE_LIMIT_BURST", "2000")))
    jobs = JobManager(gw, workers=workers)

    @asynccontextmanager
    async def lifespan(app):
        jobs.start()
        yield
        await jobs.stop()

    app = FastAPI(title="inference-gateway", version="0.1.0", lifespan=lifespan)

    def tenant(x_api_key: str | None = Header(default=None)) -> str:
        if x_api_key not in keys:
            raise HTTPException(401, "invalid api key")
        ok, wait = limiter.try_acquire(keys[x_api_key])
        if not ok:
            raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": str(max(1, int(wait + 0.999)))})
        return keys[x_api_key]

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "queue_depth": jobs.queue.qsize()}

    @app.get("/metrics")
    def metrics():
        return Response(gw.metrics.render(), media_type="text/plain; version=0.0.4")

    @app.post("/v1/chat")
    async def chat(body: ChatIn, t: str = Depends(tenant)):
        try:
            r = await gw.complete(t, body.prompt, body.model_hint, body.max_tokens)
        except AllProvidersFailed as e:
            gw.metrics.inc("gateway_errors_total", kind="all_providers_failed")
            raise HTTPException(502, "all providers failed") from e
        return {"text": r.text, "model": r.model, "tier": r.tier, "latency_ms": round(r.latency_ms, 2),
                "cache": {"layer": r.cache_layer, "similarity": r.similarity, "matched_prompt": r.matched_prompt},
                "usage": {"input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "cost_usd": r.cost_usd}}

    @app.post("/v1/jobs", status_code=202)
    async def submit(body: ChatIn, t: str = Depends(tenant), idempotency_key: str | None = Header(default=None)):
        try:
            j = jobs.submit(t, body.prompt, body.model_hint, body.max_tokens, idempotency_key)
        except QueueFull as e:
            raise HTTPException(503, "queue full", headers={"Retry-After": "1"}) from e
        return {"job_id": j.id, "status": j.status}

    @app.get("/v1/jobs/{job_id}")
    async def job(job_id: str, t: str = Depends(tenant)):
        j = jobs.get(t, job_id)
        if j is None:
            raise HTTPException(404, "job not found")
        return {"job_id": j.id, "status": j.status, "result": j.result, "error": j.error}

    app.state.gateway = gw
    return app


app = create_app()
