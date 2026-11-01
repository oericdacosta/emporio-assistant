"""Tools de catálogo: busca, ficha do produto e condições de pagamento."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, model_validator

from emporio.data.repository import CsvRepository
from emporio.data.search import MAX_LIMIT, SearchHit, normalize, rank_products, search_products
from emporio.domain.availability import Availability, disclosable_stock, product_availability
from emporio.domain.models import Product
from emporio.domain.pricing import (
    Price,
    brl,
    combined_payment_allowed,
    installment_options,
    max_installments,
    money,
    price_for,
    quote,
    subtotal,
)
from emporio.tools.deps import ToolContext
from emporio.tools.registry import Money, Params, PositiveMoney, Tool, ToolError

ALTERNATIVES = 3
TIE_TOLERANCE = 1e-6


def category_slugs(repo: CsvRepository) -> dict[str, str]:
    """Valor aceito em ``category`` -> nome da categoria; gerado dos nomes do CSV."""
    return {"_".join(normalize(c.name).split()): c.name for c in repo.categories()}


def require_product(ctx: ToolContext, product_id: int) -> Product:
    product = ctx.repo.product(product_id)
    if product is None:
        raise ToolError(
            "product_not_found",
            f"não existe produto com id {product_id}",
            "Use search_products para achar o id correto.",
        )
    return product


def availability_label(product: Product) -> str:
    status = product_availability(product)
    if status is Availability.AVAILABLE:
        return "low_stock" if disclosable_stock(product) is not None else "in_stock"
    return status.value


def _price_fields(price: Price) -> dict[str, Any]:
    return {
        "list_price_brl": brl(price.list_price),
        "promo_percent": None if price.promo_percent is None else f"{price.promo_percent}%",
        "effective_price_brl": brl(price.price),
    }


def _summary(ctx: ToolContext, product: Product, price: Price | None = None) -> dict[str, Any]:
    category = ctx.repo.category(product.category_id)
    view: dict[str, Any] = {
        "product_id": product.id,
        "name": product.name,
        "category": category.name if category else "",
        **_price_fields(price or quote(product, ctx.repo.promotions_for(product.id))),
        "availability": availability_label(product),
    }
    if (stock := disclosable_stock(product)) is not None:
        view["stock_quantity"] = stock
    return view


def _candidates(hits: list[SearchHit] | tuple[SearchHit, ...]) -> list[dict[str, Any]]:
    return [{"product_id": h.product.id, "name": h.product.name} for h in hits]


# --- search_products ---------------------------------------------------------------------------


class SearchProductsParams(Params):
    query: str | None = Field(default=None, description="Marca, modelo ou termo (ex.: 'takamine')")
    category: str | None = None
    min_price_brl: Money | None = Field(default=None, description="Preço vigente mínimo")
    max_price_brl: Money | None = Field(default=None, description="Preço vigente máximo")
    only_available: bool = Field(default=True, description="true = só o que dá para vender agora")
    sort: Literal["relevance", "price_asc", "price_desc"] = "relevance"
    limit: int = Field(default=5, ge=1, le=MAX_LIMIT)


def search_products_tool(ctx: ToolContext, args: SearchProductsParams) -> dict[str, Any]:
    slugs = category_slugs(ctx.repo)
    if args.category is not None and args.category not in slugs:
        raise ToolError(
            "unknown_category",
            f"categoria desconhecida: {args.category}",
            f"Informe `category` entre: {', '.join(slugs)}.",
        )
    result = search_products(
        ctx.repo,
        args.query or "",
        category=args.category,
        min_price=args.min_price_brl,
        max_price=args.max_price_brl,
        only_available=args.only_available,
        sort=args.sort,
        limit=args.limit,
    )
    filters: dict[str, str] = {}
    if args.category is not None:
        filters["category"] = args.category
    if args.min_price_brl is not None:
        filters["min_price_brl"] = brl(args.min_price_brl)
    if args.max_price_brl is not None:
        filters["max_price_brl"] = brl(args.max_price_brl)
    shown = len(result.hits)
    data: dict[str, Any] = {
        "total_matches": result.total_matches,
        "shown": shown,
        "not_shown": result.total_matches - shown,
        "filters_applied": filters,
        "items": [_summary(ctx, h.product, h.price) for h in result.hits],
    }
    if data["not_shown"]:
        data["note"] = (
            f"Há {result.total_matches} produtos ao todo e só {shown} foram mostrados: "
            "diga o total e ofereça filtrar ou ver mais."
        )
    if result.candidates:
        data["candidates"] = _candidates(result.candidates)
    return data


# --- get_product -------------------------------------------------------------------------------


class GetProductParams(Params):
    product_id: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _one_identifier(self) -> GetProductParams:
        if (self.product_id is None) == (self.name is None):
            raise ValueError("informe product_id ou name, só um dos dois")
        return self


def _alternatives(ctx: ToolContext, product: Product, price: Price) -> list[dict[str, Any]]:
    """Itens vendáveis da mesma categoria, do preço mais próximo ao mais distante."""
    options = [
        (quote(p, ctx.repo.promotions_for(p.id)), p)
        for p in ctx.repo.products()
        if p.category_id == product.category_id
        and p.id != product.id
        and availability_label(p) in ("in_stock", "low_stock")
    ]
    options.sort(key=lambda option: (abs(option[0].price - price.price), option[1].id))
    return [
        {
            "product_id": p.id,
            "name": p.name,
            "effective_price_brl": brl(alt.price),
        }
        for alt, p in options[:ALTERNATIVES]
    ]


def _find(ctx: ToolContext, name: str) -> Product | list[SearchHit]:
    """O produto que o nome identifica, ou os candidatos empatados quando o nome é ambíguo."""
    hits = rank_products(ctx.repo, name, only_available=False)
    if not hits:
        raise ToolError(
            "product_not_found",
            f"nenhum produto do catálogo corresponde a '{name}'",
            "Use search_products para listar opções ou peça mais detalhes ao cliente.",
        )
    tied = [h for h in hits if hits[0].score - h.score <= TIE_TOLERANCE]
    return hits[0].product if len(tied) == 1 else tied[:MAX_LIMIT]


def get_product_tool(ctx: ToolContext, args: GetProductParams) -> dict[str, Any]:
    if args.product_id is not None:
        product = require_product(ctx, args.product_id)
    else:
        found = _find(ctx, args.name or "")
        if isinstance(found, list):
            return {
                "candidates": _candidates(found),
                "note": "Mais de um produto combina com o nome; pergunte ao cliente qual deles.",
            }
        product = found

    price = quote(product, ctx.repo.promotions_for(product.id))
    data = {
        **_summary(ctx, product, price),
        "pix_price_brl": brl(price.pix_price) if price.pix_discount_applied else None,
        "specs": dict(product.specs),
    }
    if availability_label(product) not in ("in_stock", "low_stock"):
        data["alternatives"] = _alternatives(ctx, product, price)
    return data


# --- calculate_payment_options -----------------------------------------------------------------


class PaymentParams(Params):
    product_id: int | None = Field(default=None, ge=1)
    amount_brl: PositiveMoney | None = Field(
        default=None, description="Valor total, quando não for de um produto do catálogo"
    )
    quantity: int = Field(default=1, ge=1, le=20, description="Só vale junto de product_id")

    @model_validator(mode="after")
    def _one_source(self) -> PaymentParams:
        if (self.product_id is None) == (self.amount_brl is None):
            raise ValueError("informe product_id ou amount_brl, só um dos dois")
        return self


def calculate_payment_options_tool(ctx: ToolContext, args: PaymentParams) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if args.product_id is not None:
        product = require_product(ctx, args.product_id)
        unit = quote(product, ctx.repo.promotions_for(product.id))
        total = subtotal([(unit, args.quantity)])
        pix = None if unit.promo_percent else price_for(unit.list_price * args.quantity).pix_price
        pix_note = (
            f"sem desconto PIX: item em promoção de {unit.promo_percent}%"
            if unit.promo_percent
            else "5% de desconto sobre o preço de tabela"
        )
        extra = {"product": product.name, "availability": availability_label(product)}
    else:
        total = money(args.amount_brl or Decimal(0))
        pix = price_for(total).pix_price
        pix_note = "5% de desconto sobre o valor informado, tratado como preço de tabela"

    plans = []
    for option in installment_options(total):
        plan = f"{option.n}x de {brl(option.installment)}"
        if option.last_installment != option.installment:
            plan += f" (a última de {brl(option.last_installment)})"
        plans.append(
            {
                "installments": f"{option.n}x",
                "installment_brl": brl(option.installment),
                "total_brl": brl(option.total),
                "plan": plan,
            }
        )
    combined = combined_payment_allowed(total)
    return {
        **extra,
        "base_price_brl": brl(total),
        "pix_total_brl": None if pix is None else brl(pix),
        "pix_note": pix_note,
        "debit_total_brl": brl(total),
        "boleto_total_brl": brl(total),
        "max_installments": f"{max_installments(total)}x",
        "installments": plans,
        "combined_payment_allowed": combined,
        "combined_payment_note": (
            "Pode combinar formas de pagamento (ex.: PIX + cartão); a divisão dos valores "
            "é feita pela equipe no fechamento da compra."
            if combined
            else "Combinar formas de pagamento só é permitido em compras acima de R$ 2.000,00."
        ),
    }


def tools(repo: CsvRepository) -> list[Tool[Any]]:
    return [
        Tool(
            "search_products",
            "Lista produtos do catálogo com preço vigente e disponibilidade. USE para opções, "
            "faixa de preço, categoria ou marca. NÃO use para detalhar um item específico "
            "(use get_product). Se `total_matches` for 0, diga que não há no catálogo; não "
            "invente. Se `not_shown` for maior que 0, há mais produtos do que os mostrados: "
            "diga o total, não só a quantidade da lista. Se vier `candidates`, pergunte ao cliente qual dos produtos ele quer. "
            "Use only_available=false para citar item esgotado, descontinuado ou em breve.",
            SearchProductsParams,
            search_products_tool,
            enums={"category": list(category_slugs(repo))},
        ),
        Tool(
            "get_product",
            "Ficha de UM produto: preço de tabela, promoção, preço vigente, PIX, "
            "disponibilidade e specs. USE sempre antes de dizer preço ou estoque de um item. "
            "Aceita product_id (vindo de search_products) ou o nome; nome ambíguo devolve "
            "`candidates` para você perguntar ao cliente. Item indisponível vem com "
            "`alternatives`.",
            GetProductParams,
            get_product_tool,
        ),
        Tool(
            "calculate_payment_options",
            "Calcula PIX (5% sobre o preço de tabela, não vale em item em promoção), débito, "
            "boleto e parcelas no crédito sem juros, respeitando a parcela mínima. USE para "
            "qualquer dúvida de condição de pagamento de um produto ou de um valor. Nunca "
            "faça essas contas você mesmo.",
            PaymentParams,
            calculate_payment_options_tool,
        ),
    ]
