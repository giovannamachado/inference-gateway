import time


class CircuitBreaker:
    """closed → (N falhas seguidas) → open → (após reset_timeout) → half_open: deixa passar 1 sonda."""

    def __init__(self, failure_threshold: int = 5, reset_timeout: float = 10.0, clock=time.monotonic):
        self.threshold, self.reset, self.clock = failure_threshold, reset_timeout, clock
        self.failures, self.opened_at, self._probing = 0, None, False

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half_open" if self.clock() - self.opened_at >= self.reset else "open"

    def allow(self) -> bool:
        s = self.state
        if s == "closed":
            return True
        if s == "half_open" and not self._probing:
            self._probing = True
            return True
        return False

    def success(self) -> None:
        self.failures, self.opened_at, self._probing = 0, None, False

    def failure(self) -> None:
        self.failures += 1
        self._probing = False
        if self.failures >= self.threshold or self.opened_at is not None:
            self.opened_at = self.clock()


class TokenBucket:
    def __init__(self, rate: float, burst: float, clock=time.monotonic):
        self.rate, self.burst, self.clock = rate, burst, clock
        self._b: dict = {}

    def try_acquire(self, key: str, cost: float = 1.0) -> tuple[bool, float]:
        """Retorna (permitido, segundos até haver token)."""
        now = self.clock()
        tokens, last = self._b.get(key, (self.burst, now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        if tokens >= cost:
            self._b[key] = (tokens - cost, now)
            return True, 0.0
        self._b[key] = (tokens, now)
        return False, (cost - tokens) / self.rate
