"""Roteamento por complexidade estimada (heurística). Substituível por classificador treinado."""
import re

_HARD = re.compile(r"\b(analis|compar|explique detalh|passo a passo|c[oó]digo|refator|resuma o (texto|documento)|por que|justifique)", re.I)


def choose_tier(prompt: str, hint: str = "auto") -> str:
    if hint in ("small", "large"):
        return hint
    return "large" if len(prompt) > 600 or _HARD.search(prompt) else "small"
