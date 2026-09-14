# Keboola — read-only MCP konektor

Adaptér publikuje osm čtecích nástrojů nad Keboola Storage API
(`/v2/storage`). Provider credentials `stack`, `storage_token` a povinný
`pii_key` přicházejí pouze v podepsaném body svázaném s instalací. `stack`
vybírá jeden z pevně daných veřejných stacků; žádná URL z credentials ani
z argumentů se nepřijímá:

| `stack` | origin |
|---|---|
| `aws-us-east-1` | `https://connection.keboola.com` |
| `aws-eu-central-1` | `https://connection.eu-central-1.keboola.com` |
| `azure-north-europe` | `https://connection.north-europe.azure.keboola.com` |
| `gcp-us-east4` | `https://connection.us-east4.gcp.keboola.com` |
| `gcp-europe-west3` | `https://connection.europe-west3.gcp.keboola.com` |

Poskytovateli se posílá jen hlavička `X-StorageApi-Token`; port 443, HTTP,
redirecty a proxy z environmentu jsou odmítnuté.

## Nástroje (vše GET)

| nástroj | endpoint |
|---|---|
| `verify_token` | `GET /v2/storage/tokens/verify` |
| `list_buckets` | `GET /v2/storage/buckets` (`include=metadata`) |
| `list_tables` | `GET /v2/storage/tables` nebo `GET /v2/storage/buckets/{bucketId}/tables` (`include`) |
| `get_table` | `GET /v2/storage/tables/{id}` |
| `preview_table` | `GET /v2/storage/tables/{tableId}/data-preview` (`format=json`, `limit` ≤ 100, `columns`) |
| `list_components` | `GET /v2/storage/components` (`componentType`) |
| `list_configurations` | `GET /v2/storage/components/{componentId}/configs` |
| `list_jobs` | `GET /v2/storage/jobs` (`limit` ≤ 100, `offset` ≤ 10000) |

Safe-test volá `GET /v2/storage/tokens/verify`. Identifikátory (`in.c-main`,
`in.c-main.orders`, `keboola.ex-db-mysql`) mají pevný tvar a jsou URL-escapované;
`..` a lomítka jsou odmítnuté. Náhled dat je omezen na 100 řádků a nikdy
nepoužívá filtry `whereFilters`/`fulltextSearch`. Žádné vytváření, import,
spouštění jobů ani mazání se neprovádí; write nástroje neexistují.

Queue API (`queue.<stack>`) je na jiném hostu než Storage API, proto není
součástí allow-listu; `list_jobs` vrací pouze Storage joby.

## Limity a PII

Upstream odpověď je omezena na 2 MiB, bezpečný výstup na 256 KiB; nad limitem
vrací adaptér chybu, nikoli zkrácená data. Neznámé texty a názvy polí jsou
pseudonymizované per uživatel/workspace/instalace/stack; jde o pseudonymizaci,
ne anonymizaci ani náhradu autorizace.

## Ověřeno podle

- https://developers.keboola.com/overview/api/ (seznam stacků a hostů,
  hlavička `X-StorageApi-Token`, oddělené hosty Queue API)
- https://connection.keboola.com/api/storage/doc.json (OpenAPI 3.0 Storage API:
  cesty, parametry `include`, `componentType`, `limit`/`offset`, `format=json`
  a `limit` ≤ 1000 u data-preview, tvar odpovědí)
- https://keboola.docs.apiary.io/ (Apiary blueprint je vyřazený a odkazuje na
  https://api.keboola.com/?service=storage, tj. stejný OpenAPI dokument)

Mock testy nenahrazují přejímku skutečného projektu. Port `8119` je jen
vývojová hodnota; produkční port, mTLS, replay ACL a credentials provisioning
přiděluje platforma.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"stack": "…", "storage_token": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_keboola local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/keboola.json \
  python -m connector_keboola local call --privacy balanced verify_token
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_keboola mcp`
nebo `docker run -i … openmcp-connector-keboola mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py keboola --credentials $HOME/.openmcp/keboola.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
