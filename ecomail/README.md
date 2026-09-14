# Ecomail — read-only MCP konektor

Adaptér publikuje osm čtecích nástrojů nad Ecomail API v2
(`https://api2.ecomailapp.cz`). Výchozí interní port je 8131.

## Nástroje

| Nástroj | Endpoint (vždy `GET`) |
| --- | --- |
| `list_lists` | `/lists` |
| `get_list` | `/lists/{list_id}` |
| `list_subscribers` | `/lists/{list_id}/subscribers` (`page`, `per_page` 1–500, `status` = `subscribed`/`unsubscribed`/`not_confirmed`/`bounced`/`complained`) |
| `get_subscriber` | `/subscribers/{email}` (globálně) nebo `/lists/{list_id}/subscriber/{email}` (při zadaném `list_id`) |
| `list_campaigns` | `/campaigns` (`per_page`, `sort_by`, `sort_dir`, `filters[id]`, `filters[title]`, `filters[subject]`, `filters[status]`, `filters[date_from]`, `filters[date_to]`) |
| `campaign_stats` | `/campaigns/{campaign_id}/stats` (`from_date`, `to_date`) |
| `list_templates` | `/templates` |
| `list_automations` | `/pipelines` |

Safe-test volá `GET /lists`. E-mail v cestě je přijat jen jako prostá
adresa (regulární výraz) a URL-kóduje se; žádná cesta ani host z argumentů
se nikdy nesestavuje jinak než z pevných dokumentovaných šablon.

## Credentials a egress

Credentials přicházejí pouze v podepsaném body svázaném s instalací:
`api_key` a nejméně 32bytový `pii_key`. API klíč se posílá výhradně
v dokumentované hlavičce, která se jmenuje doslova `key` (žádný
`Authorization`), a nikdy se neloguje. Egress: GET na
`api2.ecomailapp.cz:443`.

## Limity a ochrana dat

Provider omezuje 1 000 požadavků za minutu na API klíč; 429 (s
`Retry-After`) se mapuje na `rate_limited` (retryable, čtecí operace se
opakují nejvýše dvakrát). Stránkování `list_subscribers` je providerovo
`page`/`per_page` (provider dovoluje až 5 000, adaptér max. 500 kvůli
limitu výstupu). Upstream odpověď max. 4 MiB, výsledek max. 256 KiB, jinak
chyba (nikdy tiché zkrácení) – u `list_templates` (vrací i HTML šablon) a
velkých seznamů proto může být nutné zúžit dotaz. Neznámá pole a texty
(jména, e-maily, telefony, vlastní pole, HTML) jsou pseudonymizované přes
`private_envelope`; čitelné zůstávají jen vybraná číselná/datová/boolean
pole. Chyby providera (400/401/403/404/429/5xx, tělo s `errors`/`error`,
nevalidní JSON) se mapují na `ConnectorError` bez textu providera.

Vynecháno: zápisové operace (přihlášení/odhlášení kontaktů, tvorba a
odesílání kampaní, transakční e-maily, webhooky, tracker), `GET
/feeds/{id}/refresh` (GET s vedlejším efektem), `/campaigns/{id}/stats-detail`
a logy kontaktů (per-subscriber události – vysoká hustota osobních údajů),
`/lists/{id}/subscriber-by-phone/{phone}`. Parametr `page` u `/campaigns`
není v dokumentaci uveden, proto se neposílá.

Ověřeno podle oficiální dokumentace Ecomail API v2
(https://docs.ecomail.cz/api-reference/introduction a její OpenAPI
https://docs.ecomail.cz/openapi.json; stránky
`lists/list-all`, `lists/show`, `lists/get-subscribers`,
`lists/get-subscriber`, `subscribers/get`, `campaigns/list-all`,
`campaigns/get-stats`, `templates/list-all`, `automations/list-all`).
Starší https://ecomailczv2.docs.apiary.io/ v době ověření vracel HTTP 502.
Mock testy nenahrazují přejímku se skutečným účtem.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"api_key": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_ecomail local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/ecomail.json \
  python -m connector_ecomail local call --privacy balanced list_lists
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_ecomail mcp`
nebo `docker run -i … openmcp-connector-ecomail mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py ecomail --credentials $HOME/.openmcp/ecomail.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
