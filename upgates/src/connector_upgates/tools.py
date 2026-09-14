"""Read-only provider mappings migrated from the original Upgates connector."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from openmcp_connector_runtime import InvocationContext, ToolEnvelope
from openmcp_connector_runtime.provider import segment as _segment
from pydantic import Field

from .schemas import validate_date_range, validate_page
from .service import UpgatesService

_D_PAGE = Field(description="Číslo stránky (od 1)", ge=1, le=10000)
_D_LANGUAGE = Field(description="Jazyk ISO 639-1", min_length=2, max_length=2)
_D_DATE_FROM = Field(description="Od data YYYY-MM-DD")
_D_DATE_TO = Field(description="Do data YYYY-MM-DD")


def _path_segment(value: int | str, _label: str) -> str:
    return _segment(value)


def _query(**kwargs: object) -> dict[str, object]:
    return {key: value for key, value in kwargs.items() if value is not None}


async def list_orders(
    order_number: Annotated[str | None, Field(description="Konkrétní číslo objednávky")] = None,
    creation_time_from: Annotated[str | None, _D_DATE_FROM] = None,
    creation_time_to: Annotated[str | None, _D_DATE_TO] = None,
    last_update_time_from: Annotated[
        str | None,
        Field(description="Filtrovat objednávky aktualizované od tohoto data (YYYY-MM-DD)"),
    ] = None,
    paid_yn: Annotated[bool | None, Field(description="Filtrovat podle stavu zaplacení")] = None,
    status: Annotated[
        str | None, Field(description="Filtrovat podle názvu stavu objednávky")
    ] = None,
    status_id: Annotated[
        int | None, Field(description="Filtrovat podle ID stavu objednávky")
    ] = None,
    email: Annotated[str | None, Field(description="Filtrovat podle e-mailu zákazníka")] = None,
    phone: Annotated[
        str | None, Field(description="Filtrovat podle telefonu zákazníka (formát MSISDN)")
    ] = None,
    external_order_number: Annotated[
        str | None, Field(description="Filtrovat podle externího čísla objednávky")
    ] = None,
    language: Annotated[str | None, _D_LANGUAGE] = None,
    page: Annotated[int, _D_PAGE] = 1,
    order_by: Annotated[
        str, Field(description="Řadit podle pole: creation_time | last_update_time")
    ] = "creation_time",
    order_dir: Annotated[str, Field(description="Směr řazení: asc | desc")] = "desc",
    *,
    service: UpgatesService,
    context: InvocationContext,
) -> ToolEnvelope:
    """Seznam objednávek s filtrováním a stránkováním (s omezením velikosti odpovědi).

    Data zákazníka jsou před opuštěním konektoru pseudonymizována.
    """
    validate_page(page)
    validate_date_range(creation_time_from, creation_time_to)
    endpoint = (
        f"/orders/{_path_segment(order_number, 'order_number')}" if order_number else "/orders"
    )
    params = _query(
        order_number=order_number,
        creation_time_from=creation_time_from,
        creation_time_to=creation_time_to,
        last_update_time_from=last_update_time_from,
        paid_yn=paid_yn,
        status=status,
        status_id=status_id,
        email=email,
        phone=phone,
        external_order_number=external_order_number,
        language=language,
        page=page,
        order_by=order_by,
        order_dir=order_dir,
    )
    return await service.get(context, endpoint, params)


async def get_order_history(
    order_number: Annotated[str, Field(description="Číslo objednávky")],
    *,
    service: UpgatesService,
    context: InvocationContext,
) -> ToolEnvelope:
    """Historie konkrétní objednávky. Data zákazníka jsou pseudonymizována."""
    path = f"/orders/{_path_segment(order_number, 'order_number')}/history"
    return await service.get(context, path)


# =============================================================================
# Stavy objednávek (Order statuses)
# =============================================================================
async def list_order_statuses(
    id: Annotated[int | None, Field(description="Konkrétní ID stavu")] = None,
    type: Annotated[
        str | None,
        Field(
            description=(
                "Typ stavu: Received | Canceled | Sent | PaymentSuccessful | PaymentFailed | Custom"
            )
        ),
    ] = None,
    *,
    service: UpgatesService,
    context: InvocationContext,
) -> ToolEnvelope:
    """Seznam všech stavů objednávek."""
    endpoint = f"/order-statuses/{_path_segment(id, 'id')}" if id else "/order-statuses"
    return await service.get(context, endpoint, _query(id=id, type=type))


# =============================================================================
# Faktury (Invoices)
# =============================================================================
async def list_invoices(
    invoice_number: Annotated[str | None, Field(description="Konkrétní číslo faktury")] = None,
    creation_time_from: Annotated[str | None, _D_DATE_FROM] = None,
    creation_time_to: Annotated[str | None, _D_DATE_TO] = None,
    paid_yn: Annotated[bool | None, Field(description="Filtrovat podle stavu zaplacení")] = None,
    type: Annotated[
        str | None, Field(description="Typ faktury: invoice | creditNote | receipt")
    ] = None,
    page: Annotated[int, _D_PAGE] = 1,
    *,
    service: UpgatesService,
    context: InvocationContext,
) -> ToolEnvelope:
    """Seznam faktur s filtrováním a stránkováním. Data zákazníka jsou pseudonymizována."""
    validate_page(page)
    validate_date_range(creation_time_from, creation_time_to)
    endpoint = (
        f"/invoices/{_path_segment(invoice_number, 'invoice_number')}"
        if invoice_number
        else "/invoices"
    )
    params = _query(
        invoice_number=invoice_number,
        creation_time_from=creation_time_from,
        creation_time_to=creation_time_to,
        paid_yn=paid_yn,
        type=type,
        page=page,
    )
    return await service.get(context, endpoint, params)


# =============================================================================
# Produkty (Products)
# =============================================================================
async def list_products(
    code: Annotated[str | None, Field(description="Kód produktu")] = None,
    product_id: Annotated[int | None, Field(description="ID produktu")] = None,
    last_update_time_from: Annotated[
        str | None,
        Field(description="Filtrovat produkty aktualizované od tohoto data (YYYY-MM-DD)"),
    ] = None,
    active_yn: Annotated[
        bool | None, Field(description="Filtrovat podle stavu aktivní (výchozí: true)")
    ] = None,
    archived_yn: Annotated[
        bool | None, Field(description="Filtrovat podle stavu archivováno")
    ] = None,
    can_add_to_basket_yn: Annotated[
        bool | None, Field(description="Filtrovat podle možnosti vložit do košíku")
    ] = None,
    in_stock_yn: Annotated[
        bool | None, Field(description="Filtrovat podle skladové dostupnosti")
    ] = None,
    language: Annotated[str | None, _D_LANGUAGE] = None,
    pricelist: Annotated[str | None, Field(description="Filtrovat podle názvu ceníku")] = None,
    variants_yn: Annotated[
        bool | None, Field(description="Zahrnout varianty (výchozí: false)")
    ] = None,
    page: Annotated[int, _D_PAGE] = 1,
    *,
    service: UpgatesService,
    context: InvocationContext,
) -> ToolEnvelope:
    """Seznam produktů s filtrováním a stránkováním (s omezením velikosti odpovědi)."""
    validate_page(page)
    endpoint = f"/products/{_path_segment(code, 'code')}" if code else "/products"
    params = _query(
        code=code,
        product_id=product_id,
        last_update_time_from=last_update_time_from,
        active_yn=active_yn if active_yn is not None else True,
        archived_yn=archived_yn,
        can_add_to_basket_yn=can_add_to_basket_yn,
        in_stock_yn=in_stock_yn,
        language=language,
        pricelist=pricelist,
        variants_yn=variants_yn if variants_yn is not None else False,
        page=page,
    )
    return await service.get(context, endpoint, params)


async def list_products_simple(
    code: Annotated[str | None, Field(description="Kód produktu")] = None,
    product_id: Annotated[int | None, Field(description="ID produktu")] = None,
    last_update_time_from: Annotated[
        str | None,
        Field(description="Filtrovat produkty aktualizované od tohoto data (YYYY-MM-DD)"),
    ] = None,
    active_yn: Annotated[bool | None, Field(description="Filtrovat podle stavu aktivní")] = None,
    in_stock_yn: Annotated[
        bool | None, Field(description="Filtrovat podle skladové dostupnosti")
    ] = None,
    page: Annotated[int, _D_PAGE] = 1,
    *,
    service: UpgatesService,
    context: InvocationContext,
) -> ToolEnvelope:
    """Seznam produktů ve zjednodušeném formátu (s omezením velikosti odpovědi)."""
    validate_page(page)
    endpoint = f"/products/{_path_segment(code, 'code')}/simple" if code else "/products/simple"
    params = _query(
        code=code,
        product_id=product_id,
        last_update_time_from=last_update_time_from,
        active_yn=active_yn,
        in_stock_yn=in_stock_yn,
        page=page,
    )
    return await service.get(context, endpoint, params)


# =============================================================================
# Zákazníci (Customers)
# =============================================================================
async def list_customers(
    customer_id: Annotated[int | None, Field(description="Konkrétní ID zákazníka")] = None,
    code: Annotated[str | None, Field(description="Kód zákazníka")] = None,
    email: Annotated[str | None, Field(description="E-mail zákazníka")] = None,
    phone: Annotated[str | None, Field(description="Telefon zákazníka")] = None,
    active_yn: Annotated[
        bool | None, Field(description="Filtrovat podle stavu aktivní (výchozí: true)")
    ] = None,
    blocked_yn: Annotated[
        bool | None, Field(description="Filtrovat podle stavu blokován (výchozí: false)")
    ] = None,
    language: Annotated[str | None, _D_LANGUAGE] = None,
    pricelist: Annotated[str | None, Field(description="Filtrovat podle ceníku")] = None,
    last_update_time_from: Annotated[
        str | None,
        Field(description="Filtrovat zákazníky aktualizované od tohoto data (YYYY-MM-DD)"),
    ] = None,
    page: Annotated[int, _D_PAGE] = 1,
    *,
    service: UpgatesService,
    context: InvocationContext,
) -> ToolEnvelope:
    """Seznam zákazníků s filtrováním a stránkováním. Osobní data jsou pseudonymizována."""
    validate_page(page)
    params = _query(
        customer_id=customer_id,
        code=code,
        email=email,
        phone=phone,
        active_yn=active_yn if active_yn is not None else True,
        blocked_yn=blocked_yn if blocked_yn is not None else False,
        language=language,
        pricelist=pricelist,
        last_update_time_from=last_update_time_from,
        page=page,
    )
    return await service.get(context, "/customers", params)


# =============================================================================
# Kategorie (Categories)
# =============================================================================
async def list_categories(
    code: Annotated[str | None, Field(description="Kód kategorie")] = None,
    category_id: Annotated[int | None, Field(description="ID kategorie")] = None,
    parent_id: Annotated[
        int | None, Field(description="Filtrovat podle ID nadřazené kategorie")
    ] = None,
    active_yn: Annotated[
        bool | None, Field(description="Filtrovat podle stavu aktivní (výchozí: true)")
    ] = None,
    language: Annotated[str | None, _D_LANGUAGE] = None,
    last_update_time_from: Annotated[
        str | None,
        Field(description="Filtrovat kategorie aktualizované od tohoto data (YYYY-MM-DD)"),
    ] = None,
    page: Annotated[int, _D_PAGE] = 1,
    *,
    service: UpgatesService,
    context: InvocationContext,
) -> ToolEnvelope:
    """Seznam kategorií s filtrováním a stránkováním (s omezením velikosti odpovědi)."""
    validate_page(page)
    params = _query(
        code=code,
        category_id=category_id,
        parent_id=parent_id,
        active_yn=active_yn if active_yn is not None else True,
        language=language,
        last_update_time_from=last_update_time_from,
        page=page,
    )
    return await service.get(context, "/categories", params)


# =============================================================================
# Long-tail číselníky a katalogové zdroje
# =============================================================================
async def list_labels(
    id: Annotated[int | None, Field(description="Konkrétní ID štítku")] = None,
    type: Annotated[
        str | None, Field(description="Typ štítku: action | new | sale | custom")
    ] = None,
    page: Annotated[int, _D_PAGE] = 1,
    *,
    service: UpgatesService,
    context: InvocationContext,
) -> ToolEnvelope:
    """Seznam štítků produktů (s omezením velikosti odpovědi)."""
    validate_page(page)
    endpoint = f"/labels/{_path_segment(id, 'id')}" if id else "/labels"
    return await service.get(context, endpoint, _query(id=id, type=type, page=page))


async def list_availabilities(
    id: Annotated[int | None, Field(description="Konkrétní ID dostupnosti")] = None,
    type: Annotated[
        str | None,
        Field(description="Typ dostupnosti: OnRequest | NotAvailable | InStock | Custom"),
    ] = None,
    page: Annotated[int, _D_PAGE] = 1,
    *,
    service: UpgatesService,
    context: InvocationContext,
) -> ToolEnvelope:
    """Seznam dostupností produktů (s omezením velikosti odpovědi)."""
    validate_page(page)
    endpoint = f"/availabilities/{_path_segment(id, 'id')}" if id else "/availabilities"
    return await service.get(context, endpoint, _query(id=id, type=type, page=page))


async def list_manufacturers(
    id: Annotated[int | None, Field(description="Konkrétní ID výrobce")] = None,
    page: Annotated[int, _D_PAGE] = 1,
    *,
    service: UpgatesService,
    context: InvocationContext,
) -> ToolEnvelope:
    """Seznam výrobců (s omezením velikosti odpovědi)."""
    validate_page(page)
    endpoint = f"/manufacturers/{_path_segment(id, 'id')}" if id else "/manufacturers"
    return await service.get(context, endpoint, _query(id=id, page=page))


async def list_parameters(
    id: Annotated[int | None, Field(description="Konkrétní ID parametru")] = None,
    page: Annotated[int, _D_PAGE] = 1,
    *,
    service: UpgatesService,
    context: InvocationContext,
) -> ToolEnvelope:
    """Seznam parametrů produktů."""
    validate_page(page)
    endpoint = f"/parameters/{_path_segment(id, 'id')}" if id else "/parameters"
    return await service.get(context, endpoint, _query(id=id, page=page))


# =============================================================================
# Košíky (Carts)
# =============================================================================
async def list_carts(
    id: Annotated[int | None, Field(description="Konkrétní ID košíku")] = None,
    creation_time_from: Annotated[
        str | None,
        Field(description=("Filtrovat košíky od data YYYY-MM-DD; výchozí: posledních 7 dní")),
    ] = None,
    language: Annotated[str | None, _D_LANGUAGE] = None,
    filled_delivery_info_yn: Annotated[
        bool | None, Field(description="Filtrovat košíky s vyplněnými doručovacími údaji")
    ] = None,
    customer_logged_in_yn: Annotated[
        bool | None, Field(description="Filtrovat košíky s přihlášenými zákazníky")
    ] = None,
    page: Annotated[int, _D_PAGE] = 1,
    *,
    service: UpgatesService,
    context: InvocationContext,
) -> ToolEnvelope:
    """Seznam košíků s omezením velikosti odpovědi a pseudonymizací zákazníků.

    Bez filtru data nebo id se výchozí použijí košíky za posledních 7 dní.
    """
    validate_page(page)
    endpoint = f"/carts/{_path_segment(id, 'id')}" if id else "/carts"
    # Default: košíky za posledních 7 dní, pokud není filtr ani konkrétní id.
    if not creation_time_from and id is None:
        creation_time_from = (date.today() - timedelta(days=7)).isoformat()
    params = _query(
        id=id,
        creation_time_from=creation_time_from,
        language=language,
        filled_delivery_info_yn=filled_delivery_info_yn,
        customer_logged_in_yn=customer_logged_in_yn,
        page=page,
    )
    return await service.get(context, endpoint, params)


# =============================================================================
# Slevové kupóny (Vouchers)
# =============================================================================
async def list_vouchers(
    voucher_code: Annotated[str | None, Field(description="Konkrétní kód kupónu")] = None,
    active_yn: Annotated[
        bool | None, Field(description="Filtrovat podle stavu aktivní (výchozí: true)")
    ] = None,
    global_yn: Annotated[
        bool | None, Field(description="Filtrovat podle stavu opakovaně použitelný")
    ] = None,
    page: Annotated[int, _D_PAGE] = 1,
    *,
    service: UpgatesService,
    context: InvocationContext,
) -> ToolEnvelope:
    """Seznam slevových kupónů (s omezením velikosti odpovědi)."""
    validate_page(page)
    endpoint = (
        f"/vouchers/{_path_segment(voucher_code, 'voucher_code')}" if voucher_code else "/vouchers"
    )
    params = _query(
        voucher_code=voucher_code,
        active_yn=active_yn if active_yn is not None else True,
        global_yn=global_yn,
        page=page,
    )
    return await service.get(context, endpoint, params)


# =============================================================================
# Doprava a platby (Shipments, Payments)
# =============================================================================
async def list_shipments(
    id: Annotated[int | None, Field(description="Konkrétní ID dopravy")] = None,
    code: Annotated[str | None, Field(description="Kód dopravy")] = None,
    type: Annotated[
        str | None,
        Field(
            description=(
                "Typ dopravy: custom | ceskaPosta | slovenskaPosta | zasilkovna | dpd | ppl | gls"
            )
        ),
    ] = None,
    page: Annotated[int, _D_PAGE] = 1,
    *,
    service: UpgatesService,
    context: InvocationContext,
) -> ToolEnvelope:
    """Seznam způsobů dopravy (vícejazyčné popisy jsou zkráceny)."""
    validate_page(page)
    endpoint = f"/shipments/{_path_segment(id, 'id')}" if id else "/shipments"
    return await service.get(context, endpoint, _query(id=id, code=code, type=type, page=page))


async def list_payments(
    id: Annotated[int | None, Field(description="Konkrétní ID platby")] = None,
    code: Annotated[str | None, Field(description="Kód platby")] = None,
    type: Annotated[
        str | None,
        Field(
            description=(
                "Typ platby: cash | cashOnDelivery | command | paypal | gopay | stripe | custom"
            )
        ),
    ] = None,
    page: Annotated[int, _D_PAGE] = 1,
    *,
    service: UpgatesService,
    context: InvocationContext,
) -> ToolEnvelope:
    """Seznam platebních metod (vícejazyčné popisy jsou zkráceny)."""
    validate_page(page)
    endpoint = f"/payments/{_path_segment(id, 'id')}" if id else "/payments"
    return await service.get(context, endpoint, _query(id=id, code=code, type=type, page=page))


# =============================================================================
# Webhooky (Webhooks)
# =============================================================================
async def list_webhooks(
    id: Annotated[int | None, Field(description="Konkrétní ID webhooku")] = None,
    *,
    service: UpgatesService,
    context: InvocationContext,
) -> ToolEnvelope:
    """Seznam nakonfigurovaných webhooků."""
    endpoint = f"/webhooks/{_path_segment(id, 'id')}" if id else "/webhooks"
    return await service.get(context, endpoint)


async def list_webhook_events(
    *,
    service: UpgatesService,
    context: InvocationContext,
) -> ToolEnvelope:
    """Seznam dostupných událostí webhooku."""
    return await service.get(context, "/webhooks/events")


# =============================================================================
# Konfigurace e-shopu (Config, Languages, Owner, Status, Pricelists)
# =============================================================================
async def get_languages(
    *,
    service: UpgatesService,
    context: InvocationContext,
) -> ToolEnvelope:
    """Získej konfiguraci jazyků e-shopu."""
    return await service.get(context, "/languages")


async def get_shop_config(
    *,
    service: UpgatesService,
    context: InvocationContext,
) -> ToolEnvelope:
    """Získej konfiguraci a nastavení e-shopu."""
    return await service.get(context, "/config")


async def get_shop_owner(
    *,
    service: UpgatesService,
    context: InvocationContext,
) -> ToolEnvelope:
    """Získej fakturační údaje provozovatele e-shopu. Firemní data jsou pseudonymizována."""
    return await service.get(context, "/owner")


async def get_api_status(
    *,
    service: UpgatesService,
    context: InvocationContext,
) -> ToolEnvelope:
    """Získej stav API a seznam povolených endpointů pro aktuálního uživatele."""
    return await service.get(context, "/status")


async def list_pricelists(
    *,
    service: UpgatesService,
    context: InvocationContext,
) -> ToolEnvelope:
    """Seznam ceníků."""
    return await service.get(context, "/pricelists")


# =============================================================================
# Test spojení
