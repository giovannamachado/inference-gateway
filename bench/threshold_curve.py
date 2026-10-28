"""Curva do limiar do cache semântico: para cada limiar, taxa de acerto em paráfrases e taxa de falso positivo em negativos difíceis.

Falso positivo = o cache serviria a resposta de OUTRA pergunta (a métrica de risco do cache semântico).
Uso: python bench/threshold_curve.py  →  bench/results/threshold_curve.json + .md
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import numpy as np  # noqa: E402

from gateway.embed import HashingEmbedder  # noqa: E402
from gateway.verify import lexical_equivalent  # noqa: E402

pairs = [json.loads(line) for line in (ROOT / "bench" / "pairs.jsonl").read_text(encoding="utf-8").splitlines()]
emb = HashingEmbedder()
sims = {k: [] for k in ("light", "heavy", "hard_negative")}
ver = {k: [] for k in sims}
for p in pairs:
    sims[p["kind"]].append(float(emb.encode(p["a"]) @ emb.encode(p["b"])))
    ver[p["kind"]].append(lexical_equivalent(p["a"], p["b"]))
rows = []
for th in [round(x, 2) for x in np.arange(0.5, 0.99, 0.02)]:
    rows.append({"threshold": th, **{f"hit_{k}": round(float(np.mean(np.array(v) >= th)), 3) for k, v in sims.items() if k != "hard_negative"},
                 "false_positive_rate": round(float(np.mean(np.array(sims["hard_negative"]) >= th)), 3),
                 **{f"verified_hit_{k}": round(float(np.mean((np.array(sims[k]) >= th) & np.array(ver[k]))), 3) for k in ("light", "heavy")},
                 "verified_false_positive_rate": round(float(np.mean((np.array(sims["hard_negative"]) >= th) & np.array(ver["hard_negative"]))), 3)})
out = ROOT / "bench" / "results"
out.mkdir(exist_ok=True)
(out / "threshold_curve.json").write_text(json.dumps({"n": {k: len(v) for k, v in sims.items()}, "rows": rows,
                                                      "verifier_pass_rate": {k: round(float(np.mean(v)), 3) for k, v in ver.items()}, "mean_similarity": {k: round(float(np.mean(v)), 3) for k, v in sims.items()}}, indent=2))
md = ["| limiar | hit leve (só emb.) | hit forte (só emb.) | **FP** (só emb.) | hit leve (+verif.) | hit forte (+verif.) | **FP** (+verif.) |", "|---|---|---|---|---|---|---|"]
md += [f"| {r['threshold']} | {r['hit_light']} | {r['hit_heavy']} | {r['false_positive_rate']} | {r['verified_hit_light']} | {r['verified_hit_heavy']} | {r['verified_false_positive_rate']} |"
       for r in rows if r["threshold"] in (0.5, 0.7, 0.78, 0.8, 0.86, 0.9, 0.94, 0.98)]
(out / "threshold_curve.md").write_text("\n".join(md) + "\n")
print("\n".join(md)); print({k: (len(v), round(float(np.mean(v)), 3)) for k, v in sims.items()})

# Conjunto held-out: escrito DEPOIS de o verificador ser desenhado; nada foi ajustado a ele.
held = [json.loads(line) for line in (ROOT / "bench" / "heldout_pairs.jsonl").read_text(encoding="utf-8").splitlines()]
DEFAULT_TH = 0.8
tp = sum(1 for p in held if p["equivalent"] and emb.encode(p["a"]) @ emb.encode(p["b"]) >= DEFAULT_TH and lexical_equivalent(p["a"], p["b"]))
fp = sum(1 for p in held if not p["equivalent"] and emb.encode(p["a"]) @ emb.encode(p["b"]) >= DEFAULT_TH and lexical_equivalent(p["a"], p["b"]))
npos = sum(p["equivalent"] for p in held)
res = {"threshold": DEFAULT_TH, "hits": f"{tp}/{npos}", "false_positives": f"{fp}/{len(held) - npos}"}
(out / "heldout.json").write_text(json.dumps(res, indent=2))
print("held-out:", res)
