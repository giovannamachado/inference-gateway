"""Verificador de precisão para candidatos do cache semântico.

A similaridade de embedding gera candidatos (recall); o verificador decide (precisão). Este verificador é lexical:
exige o mesmo conjunto de termos de conteúdo (prefixo de 5 letras), então números, negações e entidades divergentes
(Pix vs cartão, 45 vs 90) bloqueiam o hit. Com embedder neural, troque por um verificador de equivalência (cross-encoder / LLM).
"""
import re
import unicodedata

_IGNORE = set("""a o as os de da do das dos em no na nos nas um uma uns umas que e para pra por com se ao aos meu minha meus minhas
seu sua eu voce vc me te ja foi ser sao esta como qual quais quanto quando onde posso pode faco fazer vai tem ter tenho isso
favor obrigado obrigada ola oi bom dia tarde vcs vcs eh""".split())


def _terms(text: str) -> set[str]:
    text = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
    return {t[:5] if not t.isdigit() else t for t in re.findall(r"[a-z0-9]+", text) if t not in _IGNORE}


def lexical_equivalent(a: str, b: str) -> bool:
    return _terms(a) == _terms(b)
