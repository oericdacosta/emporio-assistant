# Devolução por arrependimento, do conceito ao prazo do pedido

- **Tipo:** Política e pedido (não trivial)
- **Relógio simulado:** 2026-03-23T10:00:00-04:00
- **Modelo:** gpt-4.1-mini (gravado em 2026-11-01)

Primeiro a regra geral, sem pedido. Depois o agente pede a prova de identidade, verifica o pedido e confere o prazo de 7 dias contra a data de recebimento, em código.

---

**Cliente:** Me arrependi da minha compra, posso devolver meu pedido?

**Téo:** Pode sim! Você tem até 7 dias corridos após o recebimento para devolver o produto, sem precisar justificar. Ele precisa estar na embalagem original, sem uso e com todos os acessórios. Me passa o número do pedido e o e-mail do cadastro (ou os 4 últimos dígitos do telefone) para eu verificar seu caso?

> Consultas feitas: `search_policy({"topics": ["trocas_devolucoes"], "question": "posso devolver meu pedido por arrependimento?"})`

**Cliente:** É o pedido 7, recebi dia 17/02. Meu e-mail é leticia.rocha@jmail.com

**Téo:** Letícia, o prazo para devolução por arrependimento venceu em 24/02/2026, então não dá para fazer a devolução agora. Quer que eu encaminhe sua situação para a equipe analisar?

> Consultas feitas: `verify_customer({"order_id": 7, "proof": "leticia.rocha@jmail.com"})`, `check_return_eligibility({"order_id": 7, "reason": "regret", "received_on": "2026-02-17"})`
