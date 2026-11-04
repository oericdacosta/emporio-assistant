# Example 5 — Order status with identity verification

Scenario: Non-trivial case. The customer asks where the order is. The first proof is wrong, then corrected.

---

**User:** Oi, queria saber onde está meu pedido.

**Assistant:** Claro! Para eu poder verificar o status do seu pedido, me passa o número do pedido e o e-mail do cadastro ou os 4 últimos dígitos do seu telefone, por favor.

**User:** É o pedido 8, meu e-mail é lucas.mendes@jmail.com

**Assistant:** Não consegui confirmar esses dados. Pode me passar de novo o número do pedido e o e-mail cadastrado ou os 4 últimos dígitos do telefone?

**User:** Ops, errei o e-mail. O certo é anacarol.ferreira@coldmail.com

**Assistant:** Ana, seu pedido 8 foi enviado e está a caminho, com previsão de entrega para 28/02/2026. O código de rastreio é BRJL5544332BR. Quer que eu te ajude com mais alguma coisa?

---

**What's happening under the hood:** Order data is only released after `verify_customer` succeeds, with the order number plus an e-mail or the last 4 digits of the phone, checked in code. With the wrong e-mail the assistant says only that it could not confirm the data, without revealing whether the order exists or what failed. After the correct proof, `get_order_status` returns the status and the tracking code, and the customer's first name comes from the verified record.

- Turn 1: no tool call (answered without tools).
- Turn 2: `verify_customer({"order_id": 8, "proof": "lucas.mendes@jmail.com"})`
- Turn 3: `verify_customer({"order_id": 8, "proof": "anacarol.ferreira@coldmail.com"})`, `get_order_status({"order_id": 8})`

_Recorded with `gpt-4.1-mini` on 2026-11-01; simulated clock: 2026-03-23T10:00:00-04:00._
