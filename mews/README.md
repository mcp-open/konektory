# Mews — read-only MCP konektor

Adaptér publikuje šest čtecích nástrojů nad Mews Connector API
(`/api/connector/v1`). Výchozí interní port je 8117.

## Nástroje

| Nástroj | Operace (vždy `POST`) |
| --- | --- |
| `list_reservations` | `reservations/getAll/2023-06-06` (interval `ScheduledStartUtc` / `ScheduledEndUtc` / `CollidingUtc` / `CreatedUtc` / `UpdatedUtc`, `States`, `ServiceIds`) |
| `list_customers` | `customers/getAll` (`CreatedUtc`/`UpdatedUtc` nebo `CustomerIds`; `Extent.Addresses` volitelně) |
| `list_services` | `services/getAll` (`ServiceIds`, `ServiceType`) |
| `list_resources` | `resources/getAll` (`ResourceIds`, `Names`, `Extent.Inactive`) |
| `list_enterprises` | `enterprises/getAll` (`EnterpriseIds`) |
| `get_configuration` | `configuration/get` (`EnterpriseId` jen pro portfolio tokeny) |

Safe-test volá `configuration/get`. Connector API přijímá výhradně `HTTP POST`
s JSON tělem; adaptér proto povoluje POST, ale jen na těchto šest přesných
dokumentovaných čtecích operací (allow-list `ALLOWED_PATHS` v `service.py`,
testy ověřují, že žádná jiná metoda ani cesta se nikdy nevolá). Žádné
`add`/`update`/`delete` operace nejsou dostupné. Operace `enterprises/get`
v dokumentaci neexistuje; podnik se čte přes `enterprises/getAll` a
`configuration/get`.

Stránkování je providerovo `Limitation: {Count (1–100), Cursor}`. Odpověď
seznamu obsahuje `data`, `count` a `truncated`; cursor další stránky (opaque
GUID posledního záznamu) je uveden ve `warnings` jako `Další stránka:
cursor=…`, protože konzervativní pseudonymizace by ho jinak znečitelnila.
Časové intervaly jsou povinné pro rezervace, ohraničené na 3 měsíce
(dokumentované maximum) a posílají se jako `StartUtc`/`EndUtc` v UTC.

## Credentials a egress

Credentials přicházejí pouze v podepsaném body svázaném s instalací:
`client_token`, `access_token`, `environment` (`production` = `api.mews.com`,
`demo` = `api.mews-demo.com`; jiná hodnota selže, žádná volná URL) a nejméně
32bytový `pii_key`. Tokeny se posílají výhradně v JSON těle (`ClientToken`,
`AccessToken`) spolu s pevným `Client: "OpenMCP 1.0.0"`; nikdy v URL ani
hlavičce. Egress: POST na `api.mews.com:443` nebo `api.mews-demo.com:443`,
prefix `/api/connector/v1`.

## Limity a ochrana dat

Provider omezuje 200 požadavků na AccessToken za 30 s; 429 se mapuje na
`rate_limited` (retryable, čtecí operace se opakují nejvýše dvakrát). Upstream
odpověď max. 2 MiB, výsledek max. 256 KiB, jinak chyba (nikdy tiché zkrácení).
Neznámá pole a texty (jména hostů, e-maily, poznámky, GUID identifikátory)
jsou pseudonymizované přes `private_envelope`; čitelné zůstávají jen vybraná
číselná/datová/boolean pole. Chyby providera (400/401/403/404/429/5xx, tělo
s `Message`, nevalidní JSON) se mapují na `ConnectorError` bez textu providera.

Ověřeno podle oficiální dokumentace Mews Connector API
(https://docs.mews.com/connector-api — guidelines/environments,
guidelines/requests, guidelines/authentication, guidelines/pagination,
guidelines/responses a operations/reservations, customers, services,
resources, enterprises, configuration). Mock testy nenahrazují přejímku s
demo prostředím.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"client_token": "…", "access_token": "…", "environment": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_mews local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/mews.json \
  python -m connector_mews local call --privacy balanced list_reservations
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_mews mcp`
nebo `docker run -i … openmcp-connector-mews mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py mews --credentials $HOME/.openmcp/mews.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
