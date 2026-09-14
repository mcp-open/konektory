# Dotykačka — read-only MCP konektor

Tři čtecí nástroje nad Dotykačka Cloud API v2 (`https://api.dotykacka.cz/v2`):
informace o cloudu/provozovně, stránkovaný seznam objednávek a bezpečné
prodejní souhrny. Nic se nezapisuje.

Autentizace je OAuth: z `refresh_token` a `cloud_id` získá konektor pro každé
volání krátkodobý access token (`POST /signin/token`), který drží jen v paměti
(LRU/TTL cache, serializovaný refresh, právě jeden retry po 401/403) a nikdy
neloguje. Refresh token získáte v administraci Dotykačky (Integrace → API).

Pseudonymizace je fail-closed: chybějící nebo krátký `pii_key` zastaví
požadavek před voláním providera. Volný text a neznámá pole jsou v režimu
`strict` maskována; `balanced` ponechá obchodní data (částky, časy, stavy)
čitelná a maskuje osobní údaje zákazníků a obsluhy.

## Nástroje

| nástroj | popis |
|---|---|
| `get_cloud_info` | Základní údaje o cloudu a provozovnách. |
| `list_orders` | Stránkovaný seznam objednávek za období. |
| `sales_summary` | Souhrn tržeb za období bez jednotlivých položek. |

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"refresh_token": "…", "cloud_id": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_dotykacka local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/dotykacka.json \
  python -m connector_dotykacka local call --privacy balanced get_cloud_info
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_dotykacka mcp`
nebo `docker run -i … openmcp-connector-dotykacka mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py dotykacka --credentials $HOME/.openmcp/dotykacka.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
