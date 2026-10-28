# ADR 0002 — Fila de jobs em memória atrás de uma interface estreita

**Decisão.** `JobManager` (asyncio.Queue + workers) implementa `submit/get`, com idempotency key, isolamento por tenant, backpressure (503 + Retry-After quando a fila enche) e métrica de profundidade.
**Limite assumido.** Estado em memória: jobs não sobrevivem a restart e não escalam horizontalmente. Rodar mais de uma réplica exige trocar por broker (RabbitMQ/SQS) e um store de resultados — não implementado nesta versão.
**Por que assim.** O comportamento que importa testar (idempotência, backpressure, falha de um job não derruba o worker) é independente do transporte; a interface estreita torna a troca localizada.
