import asyncio

import pytest

from gateway.cache import ExactCache, SemanticCache
from gateway.core import AllProvidersFailed, Gateway
from gateway.embed import HashingEmbedder
from gateway.providers import Completion, MockProvider, ProviderError


class Flaky:
    def __init__(self, name, fail=True, delay=0.0):
        self.name, self.fail, self.delay, self.calls = name, fail, delay, 0

    async def complete(self, prompt, max_tokens=256):
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.fail:
            raise ProviderError("boom")
        return Completion(f"ok:{prompt}", 10, 10, self.name)


def make(small=None, large=None, exact=True, semantic=True, **kw):
    emb = HashingEmbedder()
    prov = {"small": small or [MockProvider("s", 0)], "large": large or [MockProvider("l", 0)]}
    return Gateway(prov, emb, ExactCache() if exact else None, SemanticCache(emb, threshold=0.9) if semantic else None, **kw)


async def test_exact_cache_hit_costs_nothing_and_skips_provider():
    p = MockProvider("s", 0)
    gw = make(small=[p])
    a = await gw.complete("t", "Qual o horário de funcionamento?")
    b = await gw.complete("t", "qual o  horário de funcionamento?")  # espaços/caixa normalizados
    assert (a.cache_layer, b.cache_layer) == ("none", "exact") and p.calls == 1
    assert b.cost_usd == 0 and gw.metrics.value("gateway_cost_avoided_usd_total", tenant="t") == pytest.approx(a.cost_usd)


async def test_semantic_hit_reports_similarity_and_matched_prompt():
    gw = make()
    await gw.complete("t", "Qual o prazo de reembolso no cartão de crédito?")
    r = await gw.complete("t", "Qual é o prazo de reembolso no cartão de crédito")
    assert r.cache_layer == "semantic" and r.similarity >= 0.9 and r.matched_prompt.startswith("Qual o prazo")


async def test_cache_is_isolated_between_tenants():
    gw = make()
    await gw.complete("A", "Qual o prazo de reembolso no cartão de crédito?")
    assert (await gw.complete("B", "Qual o prazo de reembolso no cartão de crédito?")).cache_layer == "none"


async def test_identical_concurrent_requests_are_coalesced():
    p = Flaky("s", fail=False, delay=0.05)
    gw = make(small=[p], semantic=False)
    rs = await asyncio.gather(*[gw.complete("t", "pergunta idêntica") for _ in range(10)])
    assert p.calls == 1 and sorted(r.cache_layer for r in rs).count("coalesced") == 9


async def test_fallback_to_secondary_provider_then_breaker_skips_primary():
    a, b = Flaky("a"), Flaky("b", fail=False)
    gw = make(small=[a, b], exact=False, semantic=False, retries=0)
    for i in range(7):
        r = await gw.complete("t", f"pergunta {i}")
        assert r.model == "b"
    assert a.calls == 5  # breaker abriu após 5 falhas e parou de chamar o primário
    assert ("a", "breaker_open") in r.attempts


async def test_escalates_to_large_tier_when_small_is_down():
    gw = make(small=[Flaky("s1")], large=[Flaky("l1", fail=False)], exact=False, semantic=False, retries=0)
    r = await gw.complete("t", "oi")
    assert r.tier == "large" and r.model == "l1"


async def test_all_providers_failed_does_not_poison_cache():
    gw = make(small=[Flaky("s")], large=[Flaky("l")], retries=0)
    with pytest.raises(AllProvidersFailed):
        await gw.complete("t", "oi")
    assert len(gw.exact) == 0 and not gw.inflight


async def test_cost_uses_tier_prices():
    gw = make(exact=False, semantic=False)
    small = await gw.complete("t", "oi", hint="small")
    large = await gw.complete("t", "oi", hint="large")
    assert large.cost_usd > small.cost_usd > 0
