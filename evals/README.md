# Avaliação do agente

Mede o agente com o modelo de verdade e gera um relatório versionável. Não roda no CI (custa
chamadas da API e o modelo varia); o CI só confere que o golden é coerente com os dados.

```bash
make eval                      # 1 execução por caso
make eval EVAL_REPEAT=3        # 3 por caso: um caso só passa se passar nas três
make eval-regress              # compara com evals/reports/baseline.json antes de mexer em prompt ou modelo
uv run python -m evals.run --only G1 --price-input 0.4 --price-cached 0.1 --price-output 1.6
```

A chave vem de `OPENAI_API_KEY` e o modelo de `LLM_MODEL` (ou do arquivo `.env`). Os preços por
milhão de tokens são opcionais e informados por quem roda; sem eles o relatório só traz tokens.

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
