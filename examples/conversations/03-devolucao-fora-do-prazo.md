# Example 3 — Return by regret, from the general rule to the order deadline

Scenario: Non-trivial case. The customer first asks about returns in general, with no order, and then gives an order and the delivery date.

---

**User:** Me arrependi da minha compra, posso devolver meu pedido?

**Assistant:** Pode sim! Você tem até 7 dias corridos após o recebimento para devolver o produto, sem precisar justificar. Ele precisa estar na embalagem original, sem uso e com todos os acessórios. Me passa o número do pedido e o e-mail do cadastro (ou os 4 últimos dígitos do telefone) para eu verificar seu caso?

**User:** É o pedido 7, recebi dia 17/02. Meu e-mail é leticia.rocha@jmail.com

**Assistant:** Letícia, o prazo para devolução por arrependimento venceu em 24/02/2026, então não dá para fazer a devolução agora. Quer que eu encaminhe sua situação para a equipe analisar?

---

**What's happening under the hood:** The first turn consults the policy (`search_policy`) for the general rule and asks for the order and a proof of identity. When the customer provides both, the orchestrator runs `verify_customer` and `check_return_eligibility` with `reason="regret"` and the received date the customer gave. The 7-day deadline is computed in code, against that date. The model only words the verdict, so it cannot miscount days.

- Turn 1: `search_policy({"topics": ["trocas_devolucoes"], "question": "posso devolver meu pedido por arrependimento?"})`
- Turn 2: `verify_customer({"order_id": 7, "proof": "leticia.rocha@jmail.com"})`, `check_return_eligibility({"order_id": 7, "reason": "regret", "received_on": "2026-02-17"})`

_Recorded with `gpt-4.1-mini` on 2026-11-01; simulated clock: 2026-03-23T10:00:00-04:00._
