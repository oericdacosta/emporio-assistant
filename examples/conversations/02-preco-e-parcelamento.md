# Preço de um produto e condições de pagamento

- **Tipo:** Preço e pagamento
- **Relógio simulado:** 2026-03-23T10:00:00-04:00
- **Modelo:** gpt-4.1-mini (gravado em 2026-11-01)

O preço vem do catálogo; as parcelas e o Pix são calculados em código, não pelo modelo.

---

**Cliente:** Quanto custa o Takamine GD20?

**Téo:** O Takamine GD20 Dreadnought Natural custa R$ 2.199,00, ou R$ 2.089,05 no PIX. Quer saber as opções de parcelamento?

> Consultas feitas: `get_product({"name": "Takamine GD20"})`

**Cliente:** Dá pra parcelar em 10x? E no Pix tem desconto?

**Téo:** No cartão, dá para parcelar em até 12x, e em 10x fica 10x de R$ 219,90 sem juros. No PIX tem 5% de desconto, saindo por R$ 2.089,05. Quer mais alguma coisa?

> Consultas feitas: `calculate_payment_options({"product_id": 95})`
