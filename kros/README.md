# KROS Fakturácia — read-only MCP konektor

Adaptér publikuje osm čtecích nástrojů nad KROS OpenAPI
(`https://api-economy.kros.sk/api`), tj. API prepojenie služeb KROS Fakturácia
a KROS Sklad. Provider credentials `api_token` a povinný `pii_key` přicházejí
pouze v podepsaném body svázaném s instalací. Token se generuje v nastavení
firmy – API prepojenia (balík s funkcí API prepojenie) a posílá se výhradně
v hlavičce `Authorization: Bearer <token>`.

## Nástroje (vše GET)

| nástroj | endpoint |
|---|---|
| `list_invoices` | `GET /api/invoices` (`IssueDateFrom/To`, `DeliveryDateFrom/To`, `PaymentStatus`, `DueDateStatus`, `OrderNumber`, `NumberingSequence`, `DocumentNumberFrom/To`, `LastModifiedTimestamp`, `ExtendedFields`, `Top`, `Skip`) |
| `get_invoice` | `GET /api/invoices/{id}` |
| `list_proforma_invoices` | `GET /api/proforma-invoices` (stejné filtry bez data dodání) |
| `list_expenses` | `GET /api/expenses` (`IssueDateFrom/To`, `DueDateFrom/To`, `PaymentStatus`, `NumberingSequence`, `DocumentNumberFrom/To`, `LastModifiedTimestamp`, `Top`, `Skip`) |
| `get_expense` | `GET /api/expenses/{id}` (Guid) |
| `list_catalog_items` | `GET /api/catalog-items` (`ItemCode`, `Name`, `OnlyMarkedForEshop`, `CatalogItemChangedTimestamp`, `Top`, `Skip`) |
| `list_payments` | `GET /api/payments` (`PaymentDateFrom/To`, `AccountId`, `ExternalId`, `LastModifiedTimestamp`, `Top`, `Skip`) |
| `list_bank_accounts` | `GET /api/payments/accounts` |

Safe-test volá `GET /api/numberingSequences` (číselné řady firmy; `GET
/api/auth/check` vrací prázdné tělo, které SDK klient nepřijímá).

Stránkování je dokumentované `Top`/`Skip` (`Top` ≤ 100, default 50). Stavy se
zadávají jako enum (`payment_status`: `not_paid`/`fully_paid` → `0`/`1`,
`due_date_status`: `in_due`/`over_due` → `1`/`2`). Data jsou `YYYY-MM-DD`,
`last_modified_from` je `YYYY-MM-DDTHH:MM:SS`; hodnoty se ověřují jako platná
data a `od` ≤ `do`. `extended_fields` dovolí jen dokumentované hodnoty `Items`,
`VatBreakdowns`, `Payments`, `LinkedDocuments`. Odpovědi se čtou z dokumentované
obálky `{"data": ...}`.

Argument `last_modified_from` se u `list_catalog_items` překládá na
dokumentovaný parametr `CatalogItemChangedTimestamp`, u ostatních seznamů na
`LastModifiedTimestamp`.

## Limity a PII

Upstream odpověď je omezena na 2 MiB, bezpečný výstup na 256 KiB; nad limitem
vrací adaptér chybu, nikoli zkrácená data. Neznámé texty a názvy polí jsou
pseudonymizované per uživatel/workspace/instalace; jde o pseudonymizaci, ne
anonymizaci. KROS omezuje API na 10 požadavků/s a 300/min (429) a HTTP 402
znamená chybějící balík s API prepojením.

## Záměrně vynecháno

Žádné zápisy: `POST /api/invoices`, `/batch`, `/single`, `/api/payments/batch`,
zakládání účtů, skladové pohyby (`/api/movements/*/single`), webhooky
(`/api/integration-subscription/poll`). Nevrací se PDF reporty ani
`previewLink` a přílohy (`/api/attachments`, binární obsah). KROS OpenAPI
nemá samostatný endpoint pro seznam partnerů/zákazníků — partner je součástí
detailu dokladu (`partner`). Dodací listy, přijaté objednávky, sklady, tagy a
skladové pohyby (GET) jsou dokumentované, ale nejsou zahrnuté, aby zůstal
rozsah 8 nástrojů; lze je doplnit stejným vzorem.

## Ověřeno podle

- https://akademia.kros.sk/faq/fakturacia/prepojenie-s-e-shopom/ (odkazy na
  OpenAPI dokumentaci a Swagger, podmínka balíku s API prepojením)
- https://www.kros.sk/openapi-dokumentacia/ (base URL
  `https://api-economy.kros.sk/api/{resource}`, `Authorization: Bearer`,
  HTTPS, JSON/UTF-8, ISO 8601, `top`/`skip` ≤ 100, limity 10/s a 300/min,
  kódy 401/402/429)
- https://api-economy.kros.sk/swagger/ → `swagger/Api endpoints/swagger.json`
  (OpenAPI 3.0.1 „KROS OpenAPI 1.0“: cesty, query parametry, typy id,
  enum hodnoty, obálka `data`, security scheme `Bearer`)

Mock testy nenahrazují přejímku skutečného účtu.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"api_token": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_kros local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/kros.json \
  python -m connector_kros local call --privacy balanced list_invoices
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_kros mcp`
nebo `docker run -i … openmcp-connector-kros mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py kros --credentials $HOME/.openmcp/kros.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
