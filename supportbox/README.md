# SupportBox — read-only MCP konektor

Adaptér publikuje šest čtecích nástrojů nad SupportBox API v2
(`https://app.supportbox.cz/api/rest/v2`). Výchozí interní port je 8116.

## Nástroje

| Nástroj | Endpoint |
| --- | --- |
| `list_mail_tickets` | `GET /api/rest/v2/mail-tickets` (filtry `status`, `mailbox_id`, `assigned_user_id`/`unassigned`, `tag_id`, `created_at gte/lte`, `last_message_at gte/lte`) |
| `get_mail_ticket` | `GET /api/rest/v2/mail-tickets?filter[id][eq]={id}` (API nemá samostatný detail) |
| `list_mail_ticket_messages` | `GET /api/rest/v2/mail-tickets/{id}/messages` |
| `list_mailboxes` | `GET /api/rest/v2/mailboxes` |
| `list_users` | `GET /api/rest/v2/users` |
| `list_tags` | `GET /api/rest/v2/tags` |

Safe-test volá `GET /api/rest/v2/users?page=1&per_page=1`. Stránkování je
providerovo `page` (1–10000) a `per_page` (1–50, výchozí 25); filtry používají
dokumentovanou syntaxi `filter[field][operator]=value`. Řazení API nepodporuje.
Odpověď obsahuje kompletní `items` a čitelná metadata stránky (`page`,
`limit`, `total`, `pages`).

## Credentials a egress

Credentials přicházejí pouze v podepsaném body svázaném s instalací:
`api_token` (API klíč uživatele SupportBoxu, posílá se jen jako
`Authorization: Bearer`) a nejméně 32bytový `pii_key`. Klíč přebírá oprávnění
uživatele, který ho vytvořil (běžný uživatel vidí jen své schránky). Egress je
pouze GET na pevný origin `app.supportbox.cz:443/api/rest/v2`; žádná URL z tool
arguments. Telefonní a chatové tikety ani stahování příloh/nahrávek adaptér
nezpřístupňuje.

## Limity a ochrana dat

Provider omezuje API na 20 požadavků za minutu na uživatele; 429 se mapuje na
`rate_limited` (retryable). Upstream odpověď max. 1 MiB, výsledek max. 256 KiB,
jinak chyba (nikdy tiché zkrácení). Neznámá pole a texty (předměty, e-maily,
jména, obsah zpráv) jsou pseudonymizované přes `private_envelope`. Chyby
providera (401/404/422/429/5xx, nevalidní JSON) se mapují na `ConnectorError`
bez textu providera.

Ověřeno podle oficiální OpenAPI specifikace „Supportbox API V2“ 2.4.0
(https://app.swaggerhub.com/apis-docs/SupportBox/SupportBox-public, odkazovaná
z https://podpora.supportbox.cz/help/api-v-supportboxu). Mock testy nenahrazují
přejímku skutečného účtu.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"api_token": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_supportbox local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/supportbox.json \
  python -m connector_supportbox local call --privacy balanced list_mail_tickets
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_supportbox mcp`
nebo `docker run -i … openmcp-connector-supportbox mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py supportbox --credentials $HOME/.openmcp/supportbox.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
