"""Cache exato (LRU + TTL) e cache semântico (cosseno sobre matriz numpy, escopo por namespace)."""
import time
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np


class ExactCache:
    def __init__(self, max_entries: int = 10_000, ttl: float = 3600.0, clock=time.monotonic):
        self.max, self.ttl, self.clock = max_entries, ttl, clock
        self._d: OrderedDict = OrderedDict()

    def get(self, key):
        item = self._d.get(key)
        if item is None:
            return None
        value, expires = item
        if self.clock() >= expires:
            del self._d[key]
            return None
        self._d.move_to_end(key)
        return value

    def put(self, key, value) -> None:
        self._d[key] = (value, self.clock() + self.ttl)
        self._d.move_to_end(key)
        while len(self._d) > self.max:
            self._d.popitem(last=False)

    def __len__(self):
        return len(self._d)


@dataclass
class SemanticHit:
    value: object
    similarity: float
    prompt: str


class SemanticCache:
    """Um índice por namespace (tenant, tier): isolamento evita servir resposta de um cliente a outro.

    Capacidade fixa em buffer circular; busca por força bruta (O(N·d)), adequada até ~10^4 entradas.
    """

    def __init__(self, embedder, threshold: float = 0.9, capacity: int = 2000, ttl: float = 3600.0, clock=time.monotonic, verifier=None, top_k: int = 3):
        self.emb, self.threshold, self.capacity, self.ttl, self.clock = embedder, threshold, capacity, ttl, clock
        self.verifier, self.top_k = verifier, top_k  # verifier(prompt_novo, prompt_cacheado) -> bool
        self._ns: dict = {}

    def _space(self, ns):
        if ns not in self._ns:
            self._ns[ns] = {"m": np.zeros((self.capacity, self.emb.dim), dtype=np.float32), "meta": [None] * self.capacity, "n": 0, "next": 0}
        return self._ns[ns]

    def lookup(self, ns, vec: np.ndarray, prompt: str | None = None) -> SemanticHit | None:
        sp = self._ns.get(ns)
        if sp is None or sp["n"] == 0:
            return None
        sims = sp["m"][: sp["n"]] @ vec
        for i in np.argsort(-sims)[: self.top_k]:
            if sims[i] < self.threshold:
                break
            meta = sp["meta"][i]
            if self.clock() >= meta["expires"]:
                continue
            if self.verifier is not None and not self.verifier(prompt, meta["prompt"]):
                continue
            return SemanticHit(meta["value"], float(sims[i]), meta["prompt"])
        return None

    def best_similarity(self, ns, vec: np.ndarray) -> float:
        sp = self._ns.get(ns)
        return float((sp["m"][: sp["n"]] @ vec).max()) if sp and sp["n"] else 0.0

    def put(self, ns, vec: np.ndarray, prompt: str, value) -> None:
        sp = self._space(ns)
        i = sp["next"]
        sp["m"][i] = vec
        sp["meta"][i] = {"prompt": prompt, "value": value, "expires": self.clock() + self.ttl}
        sp["next"] = (i + 1) % self.capacity
        sp["n"] = min(sp["n"] + 1, self.capacity)

    def size(self) -> int:
        return sum(sp["n"] for sp in self._ns.values())
