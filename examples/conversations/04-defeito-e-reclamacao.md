# Example 4 — Defective product: the store rule and a complaint protocol

Scenario: Non-trivial case. A customer reports a defect, does not have the order number at hand and asks the store to call.

---

**User:** O ukulele que comprei chegou com defeito na tarraxa. Comprei faz uns 10 dias.

**Assistant:** Sinto muito pelo problema no ukulele. Produtos com defeito de fabricação podem ser trocados em até 30 dias corridos após a compra. Para eu ajudar, me passa o número do pedido e o e-mail do cadastro (ou os 4 últimos dígitos do telefone)?

**User:** Não lembro o número do pedido agora. Podem me ligar? Meu telefone é (67) 99123-4567

**Assistant:** Anotei seu contato para a equipe ligar e ajudar com o defeito no ukulele. Seu protocolo é EMP-7524697C, e eles retornam em até 24 horas úteis. Se lembrar do número do pedido, me avisa que agilizo aqui!

---

**What's happening under the hood:** `search_policy` brings the defect rule (exchange within 30 days of the purchase), and the assistant opens with empathy before asking for the order. Since the customer cannot give the order, the assistant registers the case with `register_complaint` and a phone contact, and answers with the protocol and the 24 business hours return time, which come from the tool result. The complaint is stored locally and the forwarding to management is simulated.

- Turn 1: `search_policy({"topics": ["trocas_devolucoes"]})`
- Turn 2: `register_complaint({"summary": "O ukulele que comprei chegou com defeito na tarraxa. Comprei faz uns 10 dias.", "contact": "(67) 99123-4567"})`

_Recorded with `gpt-4.1-mini` on 2026-11-01; simulated clock: 2026-03-23T10:00:00-04:00._
