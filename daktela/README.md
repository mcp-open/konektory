# Daktela — read-only MCP konektor

Adaptér publikuje sedm čtecích nástrojů nad Daktela V6 REST API
(`https://{tenant}.daktela.com/api/v6`). Výchozí interní port je 8115.

## Nástroje

| Nástroj | Endpoint |
| --- | --- |
| `list_tickets` | `GET /api/v6/tickets.json` (filtr `stage`, `priority`, `category`, `user`, `contact`, `title contains`, `created gte/lte`; řazení `created`) |
| `get_ticket` | `GET /api/v6/tickets/{name}.json` |
| `list_activities` | `GET /api/v6/activities.json` (filtr `ticket`, `type`, `action`, `queue`, `user`; řazení `time`) |
| `list_contacts` | `GET /api/v6/contacts.json` (filtr `lastname contains`, `account`) |
| `get_contact` | `GET /api/v6/contacts/{name}.json` |
| `list_queues` | `GET /api/v6/queues.json` (filtr `type`) |
| `list_users` | `GET /api/v6/users.json` |

Safe-test volá `GET /api/v6/whoim.json`. Stránkování je providerovo `take`
(1–100) a `skip` (0–10000); filtry používají dokumentovanou syntaxi
`filter[i][field|operator|value]` spojenou operátorem AND a `sort[0][field|dir]`.
Odpověď je rozbalený `result` (u seznamů `data` + `total`).

## Credentials a egress

Credentials přicházejí pouze v podepsaném body svázaném s instalací:
`instance_url` (jen `https://{tenant}.daktela.com`, jeden DNS label, port 443,
bez cesty), `access_token` (statický token) a nejméně 32bytový `pii_key`.
Token se posílá výhradně v hlavičce `X-AUTH-TOKEN` (doporučený způsob v
dokumentaci); nikdy ne jako query parametr `accessToken`. Egress je pouze GET
na `{tenant}.daktela.com:443/api/v6`; žádná URL z tool arguments.

## Limity a ochrana dat

Upstream odpověď max. 1 MiB, výsledek max. 256 KiB, jinak chyba (nikdy tiché
zkrácení). Neznámá pole a texty (názvy tiketů, jména, e-maily, poznámky) jsou
pseudonymizované přes `private_envelope`; čitelné zůstávají jen vybraná
číselná/datová pole. Chyby providera (401/403/404/429/5xx, `error` v
odpovědi) se mapují na `ConnectorError` bez textu providera.

Ověřeno podle oficiální dokumentace Daktela V6 API
(https://customer.daktela.com/external/apihelp/v6/ — sekce General Information,
Paging/Sorting/Filtering, modely Tickets, Activities, Contacts, Queues, Users,
Working with Users → Who am I). Mock testy nenahrazují přejímku skutečné
instance.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"instance_url": "…", "access_token": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_daktela local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/daktela.json \
  python -m connector_daktela local call --privacy balanced list_tickets
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_daktela mcp`
nebo `docker run -i … openmcp-connector-daktela mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py daktela --credentials $HOME/.openmcp/daktela.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
