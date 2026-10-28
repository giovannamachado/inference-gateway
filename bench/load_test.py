"""Teste de carga reprodutível: mesmo workload contra 4 configurações do gateway (processo uvicorn real, HTTP real).

IMPORTANTE: os provedores são SIMULADOS (latência 150 ms small / 600 ms large ±20%, preços ilustrativos). O benchmark mede o
efeito do cache/coalescência/resiliência e o overhead do gateway, NÃO a latência ou o custo de um LLM real.
Uso: python bench/load_test.py [--n 1500] [--concurrency 32]  →  bench/results/load_test.json + .md
"""
import argparse
import asyncio
import json
import os
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
FAMILIES = [
    "Qual o prazo de reembolso {x}?", "Como cancelo um pedido {x}?", "Qual a taxa de entrega {x}?", "Quanto tempo leva a análise {x}?",
    "Existe pedido mínimo {x}?", "O cupom vale {x}?", "Posso pagar {x}?", "Como acompanho a entrega {x}?", "Qual a política de troca {x}?",
    "Tem desconto {x}?", "Como altero o endereço {x}?", "Quando o valor volta {x}?", "Qual o limite de uso {x}?", "Como solicito nota fiscal {x}?",
    "Qual o horário de atendimento {x}?", "Compare e justifique a melhor opção de pagamento {x}", "Explique detalhadamente o processo de estorno {x}",
    "Como funciona a gorjeta {x}?", "Qual a validade {x}?", "Como falo com o suporte {x}?",
]
ENTITIES = ["no cartão de crédito", "no Pix", "no vale-refeição"]
BASES = [f.format(x=e) for f in FAMILIES for e in ENTITIES]


def light_variant(p: str, rng: random.Random) -> str:
    return rng.choice([p.lower(), "Por favor, " + p[0].lower() + p[1:], p.rstrip("?") + "??", p.replace("Qual o", "Qual é o"), p + " Obrigado!"])


def workload(n: int, seed: int = 7):
    rng = random.Random(seed)
    weights = [1 / (i + 1) ** 1.1 for i in range(len(BASES))]
    out = []
    for _ in range(n):
        b = rng.choices(range(len(BASES)), weights)[0]
        out.append((b, light_variant(BASES[b], rng) if rng.random() < 0.35 else BASES[b]))
    return out


async def run_scenario(name, env, n, conc, port):
    e = {**os.environ, **env, "PYTHONPATH": str(ROOT / "src"), "RATE_LIMIT_RPS": "100000", "RATE_LIMIT_BURST": "100000"}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "gateway.app:app", "--port", str(port), "--log-level", "warning"], env=e, cwd=ROOT)
    base = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient(base_url=base, headers={"x-api-key": "dev-key"}, limits=httpx.Limits(max_connections=conc * 2), timeout=30) as c:
            for _ in range(100):
                try:
                    if (await c.get("/healthz")).status_code == 200:
                        break
                except httpx.HTTPError:
                    await asyncio.sleep(0.1)
            sem, lats, layers, errors, cost, wrong = asyncio.Semaphore(conc), [], {}, 0, 0.0, 0
            wl = workload(n)
            base_of = {prompt: b for b, prompt in wl}  # todo prompt enviado → pergunta-base (rótulo verdadeiro)

            async def one(b, prompt):
                nonlocal errors, cost, wrong
                async with sem:
                    t = time.perf_counter()
                    try:
                        r = await c.post("/v1/chat", json={"prompt": prompt})
                    except httpx.HTTPError:
                        errors += 1
                        return
                    dt = time.perf_counter() - t
                if r.status_code != 200:
                    errors += 1
                    return
                d = r.json()
                lats.append(dt * 1000)
                layer = d["cache"]["layer"]
                layers[layer] = layers.get(layer, 0) + 1
                cost += d["usage"]["cost_usd"]
                if layer == "semantic":  # resposta de OUTRA pergunta base = falso positivo servido
                    wrong += base_of[d["cache"]["matched_prompt"]] != b

            t0 = time.perf_counter()
            await asyncio.gather(*[one(b, p) for b, p in wl])
            wall = time.perf_counter() - t0
        q = statistics.quantiles(lats, n=100)
        return {"scenario": name, "requests": n, "errors": errors, "throughput_rps": round(n / wall, 1), "p50_ms": round(q[49], 1), "p95_ms": round(q[94], 1),
                "p99_ms": round(q[98], 1), "layers": layers, "cost_usd": round(cost, 6), "wrong_semantic_served": wrong}
    finally:
        proc.terminate()
        proc.wait(timeout=10)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1500)
    ap.add_argument("--concurrency", type=int, default=32)
    a = ap.parse_args()
    scenarios = [("sem cache (só single-flight)", {"GATEWAY_CACHE": "none"}), ("cache exato", {"GATEWAY_CACHE": "exact"}),
                 ("exato + semântico (+verificador)", {"GATEWAY_CACHE": "semantic"}),
                 ("exato + semântico, SEM verificador", {"GATEWAY_CACHE": "semantic", "SEMANTIC_VERIFY": "0"}),
                 ("sem cache, primário com 30% de falha (chaos)", {"GATEWAY_CACHE": "none", "MOCK_FAIL_RATE": "0.3"})]
    results = []
    for i, (name, env) in enumerate(scenarios):
        r = await run_scenario(name, env, a.n, a.concurrency, 18100 + i)
        results.append(r)
        print(json.dumps(r, ensure_ascii=False), flush=True)
    base_cost = results[0]["cost_usd"]
    out = ROOT / "bench" / "results"
    out.mkdir(exist_ok=True)
    (out / "load_test.json").write_text(json.dumps({"n": a.n, "concurrency": a.concurrency, "results": results}, indent=2, ensure_ascii=False))
    md = ["| cenário | erros | req/s | p50 ms | p95 ms | p99 ms | custo (simulado) | vs. sem cache | camadas | resposta errada servida |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        md.append(f"| {r['scenario']} | {r['errors']} | {r['throughput_rps']} | {r['p50_ms']} | {r['p95_ms']} | {r['p99_ms']} | ${r['cost_usd']} | {r['cost_usd'] / base_cost - 1:+.0%} | {r['layers']} | {r['wrong_semantic_served']} |")
    (out / "load_test.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    asyncio.run(main())
