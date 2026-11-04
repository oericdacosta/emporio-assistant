# Example 1 — Catalog search that continues across questions

Scenario: A customer looks for guitars within a budget, then asks follow-ups about the same list: the cheapest one, and the installments.

---

**User:** Quais opções de violões vocês têm custando até R$1000?

**Assistant:** Temos 12 violões até R$ 1000, vou listar os 8 primeiros: Tagima Memphis AC-39 Nylon Natural por R$ 429,90, Rozini RC-104 Nylon Estudante por R$ 459,90, Shelby SN-100 Folk Aço Preto por R$ 549,00, Giannini GF-1R Aço Folk Sunburst por R$ 579,90, Yamaha C40 Nylon Natural por R$ 599,90, Giannini GN-15 Nylon Cedr Natural por R$ 689,00, Yamaha F310 Aço Natural por R$ 699,90 e Tagima Dallas Tuner Aço Natural por R$ 749,00. Quer ver mais ou filtrar por algum tipo específico? 🎸

**User:** Qual o mais barato deles?

**Assistant:** O mais barato é o Tagima Memphis AC-39 Nylon Natural, por R$ 429,90. Quer saber as condições de pagamento dele?

**User:** E se eu parcelar esse em 3x, quanto fica a parcela?

**Assistant:** No cartão, dá para parcelar o Tagima Memphis AC-39 em até 3x de R$ 143,30 sem juros. Quer que eu te passe o WhatsApp da loja para fechar?

---

**What's happening under the hood:** The assistant calls `search_products` once, with the category and the price ceiling, and the tool filters `products.csv` on category, price and `status == "active"` with stock. The follow-ups are answered from the conversation state, which remembers the products listed. The installment is never computed by the model: `calculate_payment_options` does it in code, with `Decimal`, so the number in the answer always comes from a tool.

- Turn 1: `search_products({"category": "violoes", "max_price_brl": 1000, "only_available": true, "sort": "price_asc", "limit": 8})`
- Turn 2: no tool call (answered without tools).
- Turn 3: `calculate_payment_options({"product_id": 83})`

_Recorded with `gpt-4.1-mini` on 2026-11-01; simulated clock: 2026-03-23T10:00:00-04:00._
