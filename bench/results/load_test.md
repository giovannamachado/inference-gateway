| cenário | erros | req/s | p50 ms | p95 ms | p99 ms | custo (simulado) | vs. sem cache | camadas | resposta errada servida |
|---|---|---|---|---|---|---|---|---|---|
| sem cache (só single-flight) | 0 | 212.8 | 149.8 | 210.4 | 631.1 | $0.028587 | +0% | {'none': 874, 'coalesced': 626} | 0 |
| cache exato | 0 | 283.4 | 72.2 | 293.8 | 564.6 | $0.00844 | -70% | {'none': 169, 'coalesced': 57, 'exact': 1274} | 0 |
| exato + semântico (+verificador) | 0 | 259.7 | 85.7 | 330.4 | 500.2 | $0.004098 | -86% | {'none': 74, 'coalesced': 25, 'exact': 1044, 'semantic': 357} | 0 |
| exato + semântico, SEM verificador | 0 | 254.8 | 89.9 | 322.3 | 482.8 | $0.002896 | -90% | {'none': 73, 'coalesced': 24, 'exact': 1021, 'semantic': 382} | 6 |
| sem cache, primário com 30% de falha (chaos) | 0 | 148.9 | 168.6 | 449.9 | 637.2 | $0.028349 | -1% | {'none': 878, 'coalesced': 622} | 0 |
