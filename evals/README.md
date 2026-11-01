# Avaliação do agente

Mede o agente com o modelo de verdade e gera um relatório versionável. Não roda no CI (custa
chamadas da API e o modelo varia); o CI só confere que o golden é coerente com os dados.

```bash
make eval                      # 1 execução por caso
make eval EVAL_REPEAT=3        # 3 por caso: um caso só passa se passar nas três
make eval-regression           # o portão: só a suite regression, 3 por caso; sai com 1 se algum cair
make eval-capability           # só a suite capability: mede o quanto falta, nunca quebra
make eval-regress              # compara com evals/reports/baseline.json antes de mexer em prompt ou modelo
uv run python -m evals.run --only G1 --label depois-do-ajuste   # não sobrescreve o relatório do mesmo dia
```

A chave vem de `OPENAI_API_KEY` e o modelo de `LLM_MODEL` (ou do arquivo `.env`). Os preços por
milhão de tokens são opcionais e informados por quem roda; sem eles o relatório só traz tokens.

## As duas suites

Cada caso do `golden.yaml` tem `suite:` (o padrão é `regression`).

- **regression**: o que o agente já acerta. Tem de ficar em 100%: um caso que reprova faz
  `evals.run` sair com 1 (`make eval-regression`). Como a chamada ao modelo custa API, o gate roda
  na mão ou sob demanda, não no CI.
- **capability**: os casos difíceis, ainda não dominados. Só medem o quanto falta e não quebram a
  execução. Um caso sobe para `regression` quando passa em todas as execuções por um tempo.

O relatório traz, por caso e por intenção, pass@1 (taxa por execução), pass@k (ao menos uma das k
passou) e pass^k (todas passaram, o que importa em atendimento ao cliente). Com k=3 os números são
grosseiros: um caso com 1/3 e outro com 2/3 pesam parecido, e não há intervalo de confiança.

### Estado atual (2026-11-01, `gpt-4.1-mini`, 3 execuções por caso)

- **regression: 29/29.**
- **capability: 0/1.** O M01 (PIX e 12x em sequência) é instável: no terceiro turno o modelo
  respondeu certo, caiu na resposta segura e, numa execução, citou um parcelamento que não bate
  com o do produto (10x de R$ 104,90 em vez de 12x de R$ 183,25). A causa não foi investigada:
  o relatório ainda não registra o que a guarda barrou, e ela não cobre valor de outro produto.
- Antes da análise de erros o relatório marcava 25/30. Três falhas eram do agente (G12, G16, G17,
  corrigidas na descrição das tools e no prompt) e uma era do caso (G27, que punia a regra geral
  citada corretamente). O histórico está em `reports/`.

## O golden

`golden.yaml` tem 30 casos: 27 de uma pergunta e 3 conversas de várias. Nenhum valor é digitado
à mão. `{price:95}`, `{pix:95}`, `{installment:95:12}` e os outros marcadores são calculados dos
CSVs (`facts.py`), e `assume` declara a situação dos dados de que o caso depende (produto
esgotado, promoção ativa); um teste confere. Se os dados mudarem, o golden acompanha ou avisa.

- **HARD** reprova o caso: tool chamada ou não, valor exato, termo proibido, resposta segura
  entregue no lugar da resposta, tom robótico.
- **SOFT** só é reportado: frases esperadas, que mudam de modelo para modelo, e itens de
  completude (por exemplo "sem juros").

Padrões são regex, comparados com a resposta em minúsculas e sem acento. Um item em lista é
"qualquer um destes".

## Lendo o relatório

`reports/AAAA-MM-DD_<modelo>.md` traz o resultado por caso, as falhas com a resposta do agente, os
avisos, os tokens, a latência e quantas vezes a guarda de fundamentação agiu. Um caso marcado
"instável" passou em algumas execuções e falhou em outras: o modelo não é determinístico mesmo
com temperatura zero. `latest.json` guarda o que passou, para a comparação com a referência.

O relatório não esconde falhas: as que aparecem alimentam a correção do agente. Só se muda uma
expectativa do golden quando o agente estava certo e a expectativa, estreita demais.
