# ADR 0001 — Cache semântico = similaridade (recall) + verificador (precisão), isolado por tenant

**Problema medido.** Com o embedder por hashing de n-gramas, perguntas que diferem em uma palavra-chave ("Pix" vs "cartão", 45 vs 90) têm similaridade alta: sem verificador, 14,6% dos negativos difíceis passam no limiar 0,9 e, no teste de carga, 6 de 382 hits semânticos serviram a resposta de outra pergunta (1,6%).
**Decisão.** A similaridade só gera candidatos (top-3 acima do limiar); um verificador decide. O verificador lexical exige o mesmo conjunto de termos de conteúdo (números, negações e entidades precisam coincidir). Caches são isolados por (tenant, hint): nunca se serve resposta de um cliente a outro.
**Resultados.** Carga: 0 respostas erradas em 357 hits semânticos com verificador; held-out de 15 negativos novos: 0 falsos positivos (limite superior de 95% pela regra dos três ≈ 20%: amostra pequena).
**Custo.** O verificador lexical zera os acertos em paráfrases fortes (0/20) e custa ~4 pontos de redução de custo (−86% vs −90%). É uma troca consciente de recall por precisão.
**Alternativas.** Embedding neural + cross-encoder/LLM como verificador (recupera paráfrases fortes, custa latência e uma chamada extra); só cache exato (zero risco, −70% no mesmo workload).
