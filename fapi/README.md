# FAPI — read-only MCP konektor

Adaptér publikuje osm čtecích nástrojů nad FAPI REST API
(`https://api.fapi.cz`). Provider credentials `username`, `api_key` a povinný
`pii_key` přicházejí pouze v podepsaném body svázaném s instalací. Basic
autentizace (`username:api_key`, podle dokumentace a oficiálního PHP klienta
`fapi-cz/fapi-client`) vzniká až uvnitř jednoho requestu a nikdy se neloguje.

## Nástroje (vše GET)

| nástroj | endpoint |
|---|---|
| `list_invoices` | `GET /invoices` (`number`, `variable_symbol`, `type`, `status`, `client`, `project`, `form`, `series`, `parent`, `last_invoice_id`, `search`, `create_date[0..1]`, `payday_date[0..1]`, `paid_on[0..1]`, `last_modified_after`, `order`, `limit`, `offset`) |
| `get_invoice` | `GET /invoices/{id}` |
| `list_clients` | `GET /clients` (`email`, `project`, `search`, `order`, `show_statistics`, `limit`, `offset`) |
| `get_client` | `GET /clients/{id}` (`show_statistics`) |
| `list_forms` | `GET /forms` (`project`, `search`, `show_deleted`, `order`, `limit`, `offset`) |
| `get_form` | `GET /forms/{id}` (`with_payment_methods`) |
| `list_item_templates` | `GET /item_templates` (`form`, `name`, `code`, `limit`, `offset`) |
| `list_payments` | `GET /payments` (`project`, `date`, `unpaired`, `unresolved`, `limit`, `offset`) |

Safe-test volá `GET /user` (detail přihlášeného účtu).

Stránkování je dokumentované `limit`/`offset` (`limit` ≤ 100, u `/payments`
provider sám omezuje na 100). Datové rozsahy se posílají jako dokumentované
dvouprvkové pole (`create_date[0]`, `create_date[1]`); obě hranice jsou
povinné společně a `od` ≤ `do`. Řazení přijímá jen dokumentované sloupce a
posílá se jako `order=<sloupec>`; formát směru (`asc`/`desc`) v dokumentaci
není specifikovaný, proto se směr nenabízí. Seznamy se čtou z dokumentované
obálky s klíčem zdroje (`invoices`, `clients`, `forms`, `item_templates`,
`payments`); dokumentované chybové tělo `{"message", "type"}` se mapuje na
bezpečnou chybu bez textu providera.

## Limity a PII

Upstream odpověď je omezena na 2 MiB, bezpečný výstup na 256 KiB; nad limitem
vrací adaptér chybu, nikoli zkrácená data. Neznámé texty a názvy polí jsou
pseudonymizované per uživatel/workspace/instalace/username; jde o
pseudonymizaci, ne anonymizaci. Dokumentace neuvádí rate limit; 429 je
mapováno jako `rate_limited`.

## Záměrně vynecháno

Žádné zápisy (`POST/PUT/DELETE` faktur, klientů, formulářů, položek, plateb,
odesílání e-mailů, storna, URL notifikace). Nevrací se PDF faktury
(`Accept: application/pdf`) ani QR kód (binární obsah), `invoices/count` a
`clients/count`, `items/{id}` (položka konkrétní faktury je součástí detailu
faktury), `discount-codes`, `vouchers`, `periodic-invoices`, `settings`,
`api-tokens` a `statistics` (lze doplnit stejným vzorem). Nápověda upozorňuje,
že některé API metody nemusí být v referenci popsané — takové nejsou zahrnuté.

## Ověřeno podle

- https://napoveda.fapi.cz/article/84-ovladani-fapi-pres-api-rozhrani (REST API,
  odkaz na referenci a příklady, API klíč)
- https://web.fapi.cz/api-doc/ (apidoc data `api_data.json`/`api_project.json`:
  `https://api.fapi.cz/`, `Authorization: Basic {credentials}`, hlavičky
  `Accept`/`Content-Type: application/json`, návratové kódy, chybové tělo,
  parametry endpointů `invoices`, `clients`, `forms`, `item_templates`,
  `payments`, `user`)
- https://gist.github.com/fabik/2d5cd9711efb8c2a07df (příklady bez knihovny:
  `Authorization: Basic base64(username:password)`)
- https://github.com/fapi-cz/fapi-client (oficiální PHP klient:
  `FapiClientFactory` s `https://api.fapi.cz`, Basic auth, cesty `/invoices`,
  `/clients`, `/forms`, `/item_templates`, `/payments`, `/user`)

Mock testy nenahrazují přejímku skutečného účtu.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"username": "…", "api_key": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_fapi local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/fapi.json \
  python -m connector_fapi local call --privacy balanced list_invoices
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_fapi mcp`
nebo `docker run -i … openmcp-connector-fapi mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py fapi --credentials $HOME/.openmcp/fapi.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
