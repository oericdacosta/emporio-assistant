# Dados

Os arquivos em `raw/` foram fornecidos pelo desafio técnico: seis tabelas CSV (categorias, clientes, pedidos, itens de pedido, produtos e promoções) e o manual de políticas da loja, em PDF.

`policies.md` é o manual extraído do PDF, com uma seção por capítulo, e `policies_notes.json` registra as decisões do projeto sobre as contradições do manual. Para gerar o Markdown de novo, rode `make policies` e revise o resultado.

`policy_rules.json` guarda os números do manual que o código usa (prazos, desconto do PIX, faixas de parcela, frete e horário). Ele registra o `sha256` do `policies.md` de onde saiu: se o manual mudar, `emporio --check-data` e o teste `tests/unit/data/test_policy_rules.py` falham até alguém conferir os valores e atualizar o hash (`sha256sum data/policies.md`).
