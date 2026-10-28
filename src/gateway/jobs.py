"""Jobs assíncronos: fila + pool de workers. Em memória nesta versão; a interface (submit/get) é o ponto de troca por RabbitMQ/SQS."""
import asyncio
import uuid
from dataclasses import dataclass, field

from .core import AllProvidersFailed, Gateway


class QueueFull(Exception):
    pass


@dataclass
class Job:
    id: str
    tenant: str
    prompt: str
    hint: str
    max_tokens: int
    status: str = "queued"  # queued | running | done | failed
    result: dict | None = None
    error: str | None = None
    waiters: list = field(default_factory=list, repr=False)


class JobManager:
    def __init__(self, gateway: Gateway, workers: int = 4, max_queue: int = 1000):
        self.gw, self.n_workers = gateway, workers
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue)
        self.jobs: dict[str, Job] = {}
        self.idem: dict[tuple, str] = {}
        self._tasks: list[asyncio.Task] = []

    def start(self):
        self._tasks = [asyncio.create_task(self._worker()) for _ in range(self.n_workers)]

    async def stop(self):
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def submit(self, tenant: str, prompt: str, hint="auto", max_tokens=256, idempotency_key: str | None = None) -> Job:
        if idempotency_key and (tenant, idempotency_key) in self.idem:
            return self.jobs[self.idem[(tenant, idempotency_key)]]
        job = Job(uuid.uuid4().hex, tenant, prompt, hint, max_tokens)
        try:
            self.queue.put_nowait(job.id)
        except asyncio.QueueFull as e:
            raise QueueFull() from e
        self.jobs[job.id] = job
        if idempotency_key:
            self.idem[(tenant, idempotency_key)] = job.id
        self.gw.metrics.set("gateway_queue_depth", self.queue.qsize())
        return job

    def get(self, tenant: str, job_id: str) -> Job | None:
        j = self.jobs.get(job_id)
        return j if j and j.tenant == tenant else None  # isolamento entre tenants

    async def _worker(self):
        while True:
            jid = await self.queue.get()
            job = self.jobs[jid]
            job.status = "running"
            try:
                r = await self.gw.complete(job.tenant, job.prompt, job.hint, job.max_tokens)
                job.result = {"text": r.text, "model": r.model, "tier": r.tier, "cache": r.cache_layer, "cost_usd": r.cost_usd}
                job.status = "done"
            except AllProvidersFailed as e:
                job.status, job.error = "failed", f"all_providers_failed: {e}"
            except Exception as e:  # um job ruim não pode matar o worker
                job.status, job.error = "failed", repr(e)
            finally:
                self.queue.task_done()
                self.gw.metrics.set("gateway_queue_depth", self.queue.qsize())
