import numpy as np

from gateway.cache import ExactCache, SemanticCache
from gateway.embed import HashingEmbedder
from gateway.resilience import CircuitBreaker, TokenBucket
from gateway.router import choose_tier


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_exact_cache_ttl_and_lru():
    c = Clock()
    cache = ExactCache(max_entries=2, ttl=10, clock=c)
    cache.put("a", 1); cache.put("b", 2)
    assert cache.get("a") == 1  # a vira o mais recente
    cache.put("c", 3)  # expulsa b
    assert cache.get("b") is None and cache.get("a") == 1 and cache.get("c") == 3
    c.t = 11
    assert cache.get("a") is None


def test_semantic_cache_threshold_namespace_and_capacity():
    emb = HashingEmbedder()
    sc = SemanticCache(emb, threshold=0.9, capacity=2)
    q = "qual o prazo de reembolso no cartão de crédito?"
    sc.put(("t1", "auto"), emb.encode(q), q, "R1")
    assert sc.lookup(("t1", "auto"), emb.encode(q)).value == "R1"
    assert sc.lookup(("t2", "auto"), emb.encode(q)) is None  # outro tenant não enxerga
    assert sc.lookup(("t1", "auto"), emb.encode("receita de bolo de cenoura com cobertura")) is None
    for i in range(3):  # buffer circular: capacidade 2
        sc.put(("t1", "auto"), emb.encode(f"pergunta numero {i} sobre pedidos"), f"p{i}", i)
    assert sc.size() == 2 and sc.lookup(("t1", "auto"), emb.encode(q)) is None


def test_embedding_is_normalized():
    v = HashingEmbedder().encode("olá mundo")
    assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-5


def test_circuit_breaker_cycle():
    c = Clock()
    b = CircuitBreaker(failure_threshold=2, reset_timeout=5, clock=c)
    assert b.state == "closed" and b.allow()
    b.failure(); assert b.state == "closed"
    b.failure(); assert b.state == "open" and not b.allow()
    c.t = 6
    assert b.state == "half_open" and b.allow() and not b.allow()  # só uma sonda
    b.failure(); assert b.state == "open"  # sonda falhou: reabre
    c.t = 12
    assert b.allow(); b.success(); assert b.state == "closed"


def test_token_bucket_refill():
    c = Clock()
    tb = TokenBucket(rate=1, burst=2, clock=c)
    assert tb.try_acquire("k")[0] and tb.try_acquire("k")[0]
    ok, wait = tb.try_acquire("k")
    assert not ok and 0.9 < wait <= 1.0
    c.t = 1.0
    assert tb.try_acquire("k")[0]
    assert tb.try_acquire("other")[0]  # chaves independentes


def test_router():
    assert choose_tier("qual o horário?") == "small"
    assert choose_tier("Compare os dois planos e justifique") == "large"
    assert choose_tier("x" * 700) == "large"
    assert choose_tier("Compare tudo", hint="small") == "small"


def test_lexical_verifier_blocks_entity_number_and_negation_changes():
    from gateway.verify import lexical_equivalent as eq
    assert eq("Posso pagar com Pix?", "posso pagar com pix??")
    assert eq("Como cancelo meu pedido?", "Por favor, como cancelo o meu pedido")
    assert not eq("Posso pagar com Pix?", "Posso pagar com cartão de débito?")
    assert not eq("O cupom vale por 7 dias", "O cupom vale por 30 dias")
    assert not eq("Como cancelo meu pedido?", "Como não cancelo meu pedido?")


def test_semantic_cache_verifier_rejects_false_positive_candidates():
    from gateway.verify import lexical_equivalent
    emb = HashingEmbedder()
    sc = SemanticCache(emb, threshold=0.7, verifier=lexical_equivalent)
    a, b = "Qual o prazo de reembolso no cartão de crédito?", "Qual o prazo de reembolso no Pix?"
    sc.put(("t", "auto"), emb.encode(a), a, "RA")
    assert emb.encode(a) @ emb.encode(b) >= 0.7  # sem verificador seria hit (falso positivo)
    assert sc.lookup(("t", "auto"), emb.encode(b), b) is None
    assert sc.lookup(("t", "auto"), emb.encode(a.upper()), a.upper()).value == "RA"
