# SmartEmailing — read-only MCP konektor

Adaptér publikuje osm čtecích nástrojů nad SmartEmailing API v3
(`https://app.smartemailing.cz/api/v3`). Výchozí interní port je 8130.

## Nástroje

| Nástroj | Endpoint (vždy `GET`) |
| --- | --- |
| `list_contacts` | `/contacts` (`select`, `sort`, `expand=customfields`, `limit` 1–500, `offset`, filtry `emailaddress`, `name`, `surname`, `company`, `country`, `town`, `language`, `blacklisted`) |
| `get_contact` | `/contacts/{id nebo e-mail}` (`select`, `expand=customfields`) |
| `list_contactlists` | `/contactlists` (`select`, `sort`, `limit`, `offset`) |
| `get_contactlist` | `/contactlists/{id}` (`select`) |
| `list_emails` | `/emails` (`select` – výchozí `id,name,title,created`, `sort`, `limit` max. 10, `offset`) |
| `list_newsletters` | `/newsletter` (`limit`, `offset`, `filter[id][eq]`, `filter[email_id][eq]`) |
| `newsletter_stats` | `/newsletter-stats-summary` (`select`, `limit`, `offset`) |
| `list_customfields` | `/customfields` (`select`, `sort`, `expand=customfield_options`, `limit`, `offset`) |

Safe-test volá `GET /check-credentials` (autentizovaný; `GET /ping` credentials
neověřuje, proto se nepoužívá). `select` a `sort` mají tvar dokumentovaný
providerem (`?select=id,emailaddress`, `?sort=-name,id`); u `list_emails`
jsou povolené jen dokumentované hodnoty a tělo e-mailu (`htmlbody`,
`textbody`) se vrací pouze na výslovný `select`. Odpověď providera musí
mít `status: "ok"`, jinak se mapuje na `upstream_error`.

## Credentials a egress

Credentials přicházejí pouze v podepsaném body svázaném s instalací:
`username`, `api_key` a nejméně 32bytový `pii_key`. HTTP Basic
autentizace (`username:api_key`) vzniká až uvnitř jednoho requestu a nikdy
se neloguje. Egress: GET na `app.smartemailing.cz:443`, prefix `/api/v3`.

## Limity a ochrana dat

Stránkování je providerovo `limit`/`offset` (max. 500, u `/emails` max. 10).
429 se mapuje na `rate_limited` (retryable, čtecí operace se opakují
nejvýše dvakrát). Upstream odpověď max. 2 MiB, výsledek max. 256 KiB,
jinak chyba (nikdy tiché zkrácení). Neznámá pole a texty (jména, e-maily,
telefony, vlastní pole) jsou pseudonymizované přes `private_envelope`;
čitelné zůstávají jen vybraná číselná/datová/boolean pole. Chyby providera
(400/401/403/404/429/5xx, tělo se `status != ok`, nevalidní JSON) se
mapují na `ConnectorError` bez textu providera.

Vynecháno: zápisové operace (import kontaktů, odesílání, webhooky),
`/email-statistic-events-jsonl` (JSON Lines, ne JSON), `/contactlists/{id}/contacts`
(bez dokumentovaného stránkování) a `/account-info` (jen osobní údaje účtu).

Ověřeno podle oficiální dokumentace SmartEmailing API v3
(https://www.smartemailing.cz/api/,
https://app.smartemailing.cz/docs/api/v3/index.html a její OpenAPI
https://app.smartemailing.cz/docs/api/v3/openapi.json: `/ping`,
`/check-credentials`, `/contacts`, `/contacts/{emailaddress}`,
`/contactlists`, `/contactlists/{id}`, `/emails`, `/newsletter`,
`/newsletter-stats-summary`, `/customfields`). Mock testy nenahrazují
přejímku se skutečným účtem.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"username": "…", "api_key": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_smartemailing local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/smartemailing.json \
  python -m connector_smartemailing local call --privacy balanced list_contacts
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_smartemailing mcp`
nebo `docker run -i … openmcp-connector-smartemailing mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py smartemailing --credentials $HOME/.openmcp/smartemailing.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
