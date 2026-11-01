# Produto com defeito: regra da loja e registro de protocolo

- **Tipo:** Reclamação (não trivial)
- **Relógio simulado:** 2026-03-23T10:00:00-04:00
- **Modelo:** gpt-4.1-mini (gravado em 2026-11-01)

Acolhe, explica a regra de defeito e, como o cliente não tem o pedido à mão, registra a reclamação com um contato e informa o protocolo e o prazo de retorno.

---

**Cliente:** O ukulele que comprei chegou com defeito na tarraxa. Comprei faz uns 10 dias.

**Téo:** Sinto muito pelo problema no ukulele. Produtos com defeito de fabricação podem ser trocados em até 30 dias corridos após a compra. Para eu ajudar, me passa o número do pedido e o e-mail do cadastro (ou os 4 últimos dígitos do telefone)?

> Consultas feitas: `search_policy({"topics": ["trocas_devolucoes"]})`

**Cliente:** Não lembro o número do pedido agora. Podem me ligar? Meu telefone é (67) 99123-4567

**Téo:** Anotei seu contato para a equipe ligar e ajudar com o defeito no ukulele. Seu protocolo é EMP-7524697C, e eles retornam em até 24 horas úteis. Se lembrar do número do pedido, me avisa que agilizo aqui!

> Consultas feitas: `register_complaint({"summary": "O ukulele que comprei chegou com defeito na tarraxa. Comprei faz uns 10 dias.", "contact": "(67) 99123-4567"})`
