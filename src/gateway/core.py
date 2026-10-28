"""Orquestra: rate limit → cache exato → cache semântico → coalescência → roteamento → provedores (retry/fallback/breaker)."""
import asyncio
import time
from dataclasses import dataclass, field

from .cache import ExactCache, SemanticCache
from .metrics import Registry
from .providers import Completion, ProviderError
from .resilience import CircuitBreaker
from .router import choose_tier

# Preços ILUSTRATIVOS em USD por 1M de tokens (configuráveis). Não são preços reais de nenhum provedor.
DEFAULT_PRICES = {"small": (0.25, 1.25), "large": (3.0, 15.0)}


class AllProvidersFailed(Exception):
    pass


@dataclass
class Result:
    text: str
    model: str
    tier: str
    cache_layer: str  # none | exact | semantic | coalesced
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float = 0.0
    similarity: float | None = None
    matched_prompt: str | None = None
    attempts: list = field(default_factory=list)


class Gateway:
    def __init__(self, providers: dict[str, list], embedder=None, exact: ExactCache | None = None, semantic: SemanticCache | None = None,
                 prices=None, metrics: Registry | None = None, retries: int = 1, breaker_factory=CircuitBreaker, clock=time.monotonic):
        self.providers, self.emb, self.exact, self.semantic = providers, embedder, exact, semantic
        self.prices, self.metrics, self.retries = prices or DEFAULT_PRICES, metrics or Registry(), retries
        self.breakers = {p.name: breaker_factory() for plist in providers.values() for p in plist}
        self.inflight: dict = {}
        self.spend: dict[str, float] = {}
        self.clock = clock

    def cost(self, tier: str, c: Completion) -> float:
        pin, pout = self.prices[tier]
        return (c.input_tokens * pin + c.output_tokens * pout) / 1e6

    async def _upstream(self, tier: str, prompt: str, max_tokens: int) -> tuple[Completion, str, list]:
        attempts = []
        order = [tier] + (["large"] if tier == "small" else [])  # escala small → large se todo o tier cair
        for t in order:
            for p in self.providers[t]:
                br = self.breakers[p.name]
                for attempt in range(self.retries + 1):
                    if not br.allow():
                        attempts.append((p.name, "breaker_open"))
                        break
                    try:
                        c = await p.complete(prompt, max_tokens)
                        br.success()
                        attempts.append((p.name, "ok"))
                        return c, t, attempts
                    except ProviderError:
                        br.failure()
                        attempts.append((p.name, "error"))
                self.metrics.set("gateway_breaker_open", 1.0 if br.state != "closed" else 0.0, provider=p.name)
        raise AllProvidersFailed(attempts)

    async def complete(self, tenant: str, prompt: str, hint: str = "auto", max_tokens: int = 256) -> Result:
        t0 = self.clock()
        prompt = " ".join(prompt.split())
        tier = choose_tier(prompt, hint)
        ns = (tenant, hint)
        ekey = (tenant, hint, max_tokens, prompt.lower())

        def done(res: Result, saved: float = 0.0) -> Result:
            res.latency_ms = (self.clock() - t0) * 1000
            m = self.metrics
            m.inc("gateway_requests_total", tier=res.tier, cache=res.cache_layer)
            m.observe("gateway_latency_seconds", res.latency_ms / 1000, cache=res.cache_layer)
            m.inc("gateway_cost_usd_total", res.cost_usd, tenant=tenant)
            m.inc("gateway_tokens_total", res.input_tokens, direction="in")
            m.inc("gateway_tokens_total", res.output_tokens, direction="out")
            if res.cache_layer in ("exact", "semantic", "coalesced"):
                m.inc("gateway_cost_avoided_usd_total", saved, tenant=tenant)
            return res

        if self.exact is not None:
            hit = self.exact.get(ekey)
            if hit is not None:
                return done(Result(hit.text, hit.model, hit.tier, "exact", 0, 0, 0.0), hit.cost_usd)
        vec = None
        if self.semantic is not None:
            vec = self.emb.encode(prompt)
            sh = self.semantic.lookup(ns, vec, prompt)
            if sh is not None:
                v = sh.value
                return done(Result(v.text, v.model, v.tier, "semantic", 0, 0, 0.0, similarity=sh.similarity, matched_prompt=sh.prompt), v.cost_usd)

        if ekey in self.inflight:  # single-flight: chamadas idênticas simultâneas compartilham 1 chamada ao provedor
            leader = await asyncio.shield(self.inflight[ekey])
            return done(Result(leader.text, leader.model, leader.tier, "coalesced", 0, 0, 0.0), leader.cost_usd)

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.inflight[ekey] = fut
        try:
            c, used_tier, attempts = await self._upstream(tier, prompt, max_tokens)
            res = Result(c.text, c.model, used_tier, "none", c.input_tokens, c.output_tokens, self.cost(used_tier, c), attempts=attempts)
            self.spend[tenant] = self.spend.get(tenant, 0.0) + res.cost_usd
            if self.exact is not None:
                self.exact.put(ekey, res)
            if self.semantic is not None:
                self.semantic.put(ns, vec, prompt, res)
            fut.set_result(res)
            return done(res)
        except Exception as e:
            fut.set_exception(e)
            fut.exception()  # marca como "retrieved" para não logar warning se ninguém aguardava
            raise
        finally:
            self.inflight.pop(ekey, None)
