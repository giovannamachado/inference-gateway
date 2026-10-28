"""Provedores de modelo. `MockProvider` simula latência, falhas e preço; `AnthropicProvider` usa a Messages API (não testado ao vivo)."""
import asyncio
import os
import random
from dataclasses import dataclass

import httpx


@dataclass
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


class ProviderError(Exception):
    pass


def est_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class MockProvider:
    def __init__(self, name: str, latency: float, jitter: float = 0.2, fail_rate: float = 0.0, seed: int = 0):
        self.name, self.latency, self.jitter, self.fail_rate = name, latency, jitter, fail_rate
        self.rng = random.Random(seed)
        self.calls = 0

    async def complete(self, prompt: str, max_tokens: int = 256) -> Completion:
        self.calls += 1
        await asyncio.sleep(max(0.0, self.latency * (1 + self.rng.uniform(-self.jitter, self.jitter))))
        if self.rng.random() < self.fail_rate:
            raise ProviderError(f"{self.name}: falha injetada")
        text = f"[{self.name}] resposta para: {prompt}"[: max_tokens * 4]
        return Completion(text, est_tokens(prompt), est_tokens(text), self.name)


class AnthropicProvider:
    def __init__(self, name: str, model: str, api_key: str | None = None, timeout: float = 30.0):
        self.name, self.model, self.timeout = name, model, timeout
        self.key = api_key or os.environ["ANTHROPIC_API_KEY"]
        self.client = httpx.AsyncClient(timeout=timeout)

    async def complete(self, prompt: str, max_tokens: int = 256) -> Completion:
        try:
            r = await self.client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": self.key, "anthropic-version": "2023-06-01"},
                json={"model": self.model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]},
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ProviderError(str(e)) from e
        d = r.json()
        text = "".join(b.get("text", "") for b in d["content"] if b.get("type") == "text")
        return Completion(text, d["usage"]["input_tokens"], d["usage"]["output_tokens"], self.name)
