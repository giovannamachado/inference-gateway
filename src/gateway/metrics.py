"""Registro mínimo de métricas no formato de exposição do Prometheus."""
import threading

BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


def _fmt(labels: tuple) -> str:
    return "{" + ",".join(f'{k}="{v}"' for k, v in labels) + "}" if labels else ""


class Registry:
    def __init__(self):
        self._lock = threading.Lock()
        self.counters: dict[str, dict] = {}
        self.hists: dict[str, dict] = {}
        self.gauges: dict[str, dict] = {}

    def inc(self, name: str, value: float = 1.0, **labels):
        with self._lock:
            d = self.counters.setdefault(name, {})
            k = tuple(sorted(labels.items()))
            d[k] = d.get(k, 0.0) + value

    def set(self, name: str, value: float, **labels):
        with self._lock:
            self.gauges.setdefault(name, {})[tuple(sorted(labels.items()))] = value

    def observe(self, name: str, value: float, **labels):
        with self._lock:
            k = tuple(sorted(labels.items()))
            h = self.hists.setdefault(name, {}).setdefault(k, {"b": [0] * len(BUCKETS), "sum": 0.0, "n": 0})
            for i, ub in enumerate(BUCKETS):
                if value <= ub:
                    h["b"][i] += 1
            h["sum"] += value
            h["n"] += 1

    def value(self, name: str, **labels) -> float:
        return self.counters.get(name, {}).get(tuple(sorted(labels.items())), 0.0)

    def render(self) -> str:
        out = []
        with self._lock:
            for n, d in self.counters.items():
                out.append(f"# TYPE {n} counter")
                out += [f"{n}{_fmt(k)} {v}" for k, v in d.items()]
            for n, d in self.gauges.items():
                out.append(f"# TYPE {n} gauge")
                out += [f"{n}{_fmt(k)} {v}" for k, v in d.items()]
            for n, d in self.hists.items():
                out.append(f"# TYPE {n} histogram")
                for k, h in d.items():
                    for ub, c in zip(BUCKETS, h["b"]):
                        out.append(f"{n}_bucket{_fmt(k + (('le', str(ub)),))} {c}")
                    out.append(f"{n}_bucket{_fmt(k + (('le', '+Inf'),))} {h['n']}")
                    out.append(f"{n}_sum{_fmt(k)} {h['sum']}")
                    out.append(f"{n}_count{_fmt(k)} {h['n']}")
        return "\n".join(out) + "\n"
