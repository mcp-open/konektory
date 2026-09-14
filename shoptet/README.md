# Shoptet — read-only MCP konektor

Adaptér publikuje osm čtecích nástrojů nad Shoptet API
`https://api.myshoptet.com/api` (add-on API):

| Nástroj | Endpoint providera |
|---|---|
| `list_orders` | `GET /api/orders` (`page`, `itemsPerPage` ≤ 50, `statusId`, `creationTimeFrom/To`, `changeTimeFrom/To`) |
| `get_order` | `GET /api/orders/{code}` |
| `list_products` | `GET /api/products` (`page`, `itemsPerPage` ≤ 100, `type`, `categoryGuid`, `changeTimeFrom`, `include=images`) |
| `get_product` | `GET /api/products/{guid}` |
| `get_product_by_code` | `GET /api/products/code/{code}` |
| `list_customers` | `GET /api/customers` (`page`, `itemsPerPage` ≤ 100) |
| `get_customer` | `GET /api/customers/{guid}` |
| `list_stocks` | `GET /api/stocks` |

Credentials: `access_token` (API access token doplňku, hlavička
`Shoptet-Access-Token`) a povinný `pii_key`. Token se posílá jen v hlavičce
jednoho requestu, nikdy v URL ani v logu. `test_connection` volá `GET /api/eshop`.
Egress je pevně `api.myshoptet.com:443` s prefixem `/api`, pouze `GET`.

Limity: stránka 1–10 000, položek na stránku nejvýše 50 (objednávky) nebo 100
(produkty, zákazníci; provider dovoluje až 1000, adaptér drží obálku malou),
časové filtry v ISO 8601 s posunem (`2017-12-12T22:08:01+0100`), GUID ve
formátu UUID, kódy bez lomítek. Filtry podle e-mailu či telefonu záměrně chybí.
Odpovědi procházejí `private_envelope`; jména, adresy a neznámá pole jsou
pseudonymizována. Nic se nezapisuje.

Ověřeno podle https://developers.shoptet.com/ a referenční dokumentace
https://api.docs.shoptet.com/shoptet-api/openapi (sekce orders, products,
stocks, customers, eshop) včetně oficiálního mock serveru
`https://api.docs.shoptet.com/_mock/shoptet-api/openapi/`. Premium private
API token (hlavička `Shoptet-Private-API-Token`) není podporován; snapshot,
PDF a endpointy změn nejsou publikovány. Mock testy nenahrazují přejímku
skutečného e-shopu.

## Nástroje

| nástroj | popis |
|---|---|
| `list_orders` | Stránka objednávek s filtrem stavu a data vytvoření či změny. |
| `get_order` | Detail objednávky podle jejího kódu (čísla). |
| `list_products` | Stránka produktů s filtrem typu, kategorie a data změny; volitelně s obrázky. |
| `get_product` | Detail produktu podle GUID. |
| `get_product_by_code` | Detail produktu podle kódu varianty. |
| `list_customers` | Stránka zákazníků e-shopu (pseudonymizováno). |
| `get_customer` | Detail zákazníka podle GUID (pseudonymizováno). |
| `list_stocks` | Seznam skladů e-shopu včetně výchozího skladu. |

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"access_token": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_shoptet local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/shoptet.json \
  python -m connector_shoptet local call --privacy balanced list_orders
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_shoptet mcp`
nebo `docker run -i … openmcp-connector-shoptet mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py shoptet --credentials $HOME/.openmcp/shoptet.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
