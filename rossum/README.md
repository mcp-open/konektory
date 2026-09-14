# Rossum — read-only MCP konektor

Adaptér publikuje sedm čtecích nástrojů nad Rossum API organizace
(`https://{tenant}.rossum.app/api/v1`). Provider credentials `base_url`,
`api_token` (dlouhodobý token vydaný v Rossum; přihlášení jménem a heslem se
neprovádí) a povinný `pii_key` přicházejí pouze v podepsaném body svázaném
s instalací. `base_url` musí být přesně `https://{tenant}.rossum.app/api/v1`
(jeden DNS label, port 443); jiný host, HTTP, redirecty a proxy jsou odmítnuté.
Poskytovateli se posílá jen `Authorization: Bearer <api_token>`.

## Nástroje (vše GET)

| nástroj | endpoint |
|---|---|
| `list_workspaces` | `GET /workspaces` (`page_size`, `cursor`, `name`, `ordering`) |
| `list_queues` | `GET /queues` (`page_size`, `cursor`, `workspace`, `name`, `ordering`) |
| `get_queue` | `GET /queues/{id}` |
| `list_annotations` | `GET /annotations` (`page_size`, `cursor`, `queue`, `status`, `search`, `ordering`) |
| `get_annotation` | `GET /annotations/{id}` |
| `get_annotation_content` | `GET /annotations/{id}/content` (vytěžená data, sekce a datapointy) |
| `list_documents` | `GET /documents` (`page_size`, `cursor`, `original_file_name`, `ordering`) |

Safe-test volá `GET /auth/user`. Stránkování je kurzorové podle providera:
`page_size` 1–100, `cursor` je neprůhledný podepsaný řetězec z `pagination.next`
(nikdy celá URL). Filtr `status` je uzavřený výčet stavů životního cyklu
anotace. Žádné nahrávání dokumentů, start/confirm/reject/delete anotace ani
export se neprovádí; write nástroje neexistují.

## Limity a PII

Upstream odpověď je omezena na 2 MiB, bezpečný výstup na 256 KiB; nad limitem
vrací adaptér chybu, nikoli zkrácená data. Neznámé texty a názvy polí jsou
pseudonymizované per uživatel/workspace/instalace/origin; jde o pseudonymizaci,
ne anonymizaci ani náhradu autorizace.

## Ověřeno podle

- https://rossum.app/api/docs/openapi/guides/getting-started/ (base URL
  `https://<org>.rossum.app/api/v1`, `Authorization: Bearer`, tvar `pagination`/`results`)
- https://rossum.app/api/docs/openapi/openapi-specs/openapi.json (OpenAPI 3.1: cesty,
  parametry `page_size`/`cursor`/`ordering`, filtry `queue`, `status`, `workspace`,
  `original_file_name`, výčet stavů anotace, odpověď `/annotations/{id}/content`)
- https://elis.rossum.ai/api/docs/ (rozcestník dokumentace)

Mock testy nenahrazují přejímku skutečné organizace. Port `8118` je jen
vývojová hodnota; produkční port, mTLS, replay ACL a credentials provisioning
přiděluje platforma.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"base_url": "…", "api_token": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_rossum local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/rossum.json \
  python -m connector_rossum local call --privacy balanced list_workspaces
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_rossum mcp`
nebo `docker run -i … openmcp-connector-rossum mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py rossum --credentials $HOME/.openmcp/rossum.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
