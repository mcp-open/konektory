# DokladBot — read-only MCP konektor

Adaptér publikuje osm čtecích nástrojů nad REST API účetní služby
[DokladBot](https://dokladbot.cz) (`https://dokladbot.cz/api`): doklady a jejich
detail s rozpisem DPH, souhrn DPH za období, faktury po splatnosti, přehled
hospodaření, kredity, firmy na účtu a dodavatele. Přijímá osobní API klíč
(`api_key`, prefix `dkb_live_`) a povinný `pii_key` pouze v přesně podepsaném
invocation body.

Všechny cesty jsou pevné, jediný proměnný segment je validované ID dokladu
(`[A-Za-z0-9_-]{1,64}`). Runtime volá jen `GET` na `dokladbot.cz/api`,
nesleduje redirecty, nepoužívá proxy z prostředí a omezuje vstup i
pseudonymizovaný výstup. Nevytváří doklady, faktury, podání ani platby.

## Nástroje

| nástroj | popis |
|---|---|
| `list_documents` | Stránka přijatých i vydaných dokladů a jejich stavu schválení. |
| `get_document` | Detail dokladu včetně rozpisu DPH podle sazeb. |
| `get_vat_summary` | Souhrn DPH za rok, čtvrtletí nebo měsíc: základy a daň. |
| `list_due_invoices` | Faktury po splatnosti nebo s blížící se splatností. |
| `get_overview` | Přehled hospodaření: příjmy, výdaje a počty dokladů. |
| `get_credit_balance` | Aktuální zůstatek kreditů účtu. |
| `list_companies` | Firmy na účtu včetně IČO a DIČ. |
| `list_suppliers` | Dodavatelé podle útraty. |

## Credentials

API klíč vytvoříte v dashboardu DokladBot v sekci Nastavení, API klíče. Klíč
je vázaný na účet a kdykoli ho tam zrušíte. Bezpečnostní test připojení čte
zůstatek kreditů (`GET /api/credits/balance`).

## Přejímka

Mock testy pokrývají cesty, hlavičky, stránkování, chybové stavy providera
(401, 403, 429, 5xx), odmítnutí redirectu a uzavřené vstupy. Naživo ověřeno
7. 10. 2026 proti produkčnímu API na demo účtu: všech osm nástrojů vrátilo
data v režimech `strict` i `balanced`. DokladBot má vlastní vzdálený MCP server
(`https://dokladbot.cz/api/mcp`, OAuth 2.1); tento konektor je čtecí alternativa
pro hostovaný runtime OpenMCP.

## Lokální použití

```json
{"api_key": "dkb_live_…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_dokladbot local tools
printf '%s' '{"year":2026,"quarter":3}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/dokladbot.json \
  python -m connector_dokladbot local call --privacy balanced get_vat_summary
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_dokladbot mcp`.
Podrobnosti: [docs/local-cli.md](../docs/local-cli.md), [docs/mcp-stdio.md](../docs/mcp-stdio.md).
