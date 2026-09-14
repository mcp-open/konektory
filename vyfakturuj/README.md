# Vyfakturuj.cz — read-only MCP konektor

Adaptér publikuje osm čtecích nástrojů nad REST API Vyfakturuj.cz verze 2
(`https://api.vyfakturuj.cz/2.0`). Výchozí interní port je 8123. Nikdy
nevytváří, needituje, nemaže, neodesílá ani neoznačuje doklady jako uhrazené.

## Nástroje

| Nástroj | Provider endpoint (vždy `GET`) |
|---|---|
| `list_invoices` | `/invoice/` — filtry `type`, `flags`, `id_customer`, `id_number_series`, `id_tag`, `id_parent`, `number`, `VS`, `currency`, `date_created_from/to`, `date_due`, `date_paid`, `q`, `sort`, `rows_limit`, `rows_offset` |
| `get_invoice` | `/invoice/{id}/` |
| `list_contacts` | `/contact/` — filtry `IC`, `DIC`, `name`, `mail_to`, `q`, `sort`, `rows_limit`, `rows_offset` |
| `get_contact` | `/contact/{id}/` |
| `get_template` | `/template/{id}/` (šablona / pravidelná faktura) |
| `list_payment_methods` | `/settings/payment-method/` |
| `list_number_series` | `/settings/number-series/` |
| `list_tags` | `/settings/tags/` |

Safe-test volá dokumentovaný ověřovací endpoint `GET /test/`. Seznamy vrací
`items`, `count` a `truncated` (`true`, když stránka dosáhla `rows_limit`).
Argument `variable_symbol` se překládá na provider parametr `VS`, `ic`/`dic`
na `IC`/`DIC`, `sort_by` + `sort_dir` na dokumentovanou syntaxi
`{sloupec}~{asc|desc}` (jen pevný výčet sloupců). Experimentální parametr
`filter` (BETA) adaptér záměrně nenabízí.

## Credentials a egress

`email` (přihlašovací jméno), `api_key` (heslo Basic autentizace, získává se
v administraci API) a povinný `pii_key` přicházejí jen v podepsaném body
svázaném s instalací. Basic hlavička vzniká uvnitř jednoho requestu a nikdy se
neloguje. Egress: `api.vyfakturuj.cz:443`, prefix `/2.0`, pouze `GET`.

## Limity a ochrana dat

`rows_limit` 1–100 (výchozí 20), `rows_offset` ≤ 10 000, data ve formátu
`YYYY-MM-DD` s kontrolou kalendáře, `date_created_to` ≥ `date_created_from`.
Upstream odpověď max. 2 MiB, výsledek max. 256 KiB (jinak chyba, nikdy tiché
zkrácení). Chybová obálka providera (`{"status": "error", "message": …}`),
400/401/403/404/429/5xx i nevalidní JSON se mapují na `ConnectorError` bez
textu providera. Jména, adresy, telefony, e-maily a neznámá pole jsou
pseudonymizována přes `private_envelope`.

## Co adaptér nenabízí

- Platby: API v2 nemá čtecí endpoint plateb; `POST /invoice/{id}/do/pay/` je
  zápis a není volán.
- Informace o účtu/firmě: dokumentace žádný takový endpoint neuvádí, `GET /test/`
  slouží jen k ověření přihlášení.
- Seznam šablon: dokumentován je pouze detail `GET /template/{id}/`.
- PDF dokladu a export: v OpenAPI specifikaci nejsou dokumentovány.

## Ověřeno podle

- https://www.vyfakturuj.cz/api/ (odkaz na dokumentaci a PHP SDK)
- https://api.vyfakturuj.cz/scalar/ — OpenAPI 3.1 specifikace
  `https://api.vyfakturuj.cz/webroot/openapi/vyfakturuj.json` (server
  `https://api.vyfakturuj.cz/2.0/`, `basicAuth` e-mail + API klíč, cesty
  `/invoice/`, `/invoice/{id}/`, `/contact/`, `/contact/{id}/`, `/template/{id}/`,
  `/settings/payment-method/`, `/settings/number-series/`, `/settings/tags/`,
  parametry `rows_limit`, `rows_offset`, `sort`, chybová obálka `status: error`,
  příklad `GET /test/`)

Mock testy nenahrazují přejímku skutečného účtu.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"email": "…", "api_key": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_vyfakturuj local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/vyfakturuj.json \
  python -m connector_vyfakturuj local call --privacy balanced list_invoices
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_vyfakturuj mcp`
nebo `docker run -i … openmcp-connector-vyfakturuj mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py vyfakturuj --credentials $HOME/.openmcp/vyfakturuj.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
