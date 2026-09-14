# Freelo — read-only MCP konektor

Adaptér publikuje čtyři čtecí nástroje pro seznam a detail projektů a úkolů
nad `https://api.freelo.io/v1`. Provider credentials `email`, `api_key` a
povinný `pii_key` přicházejí pouze v podepsaném body svázaném s instalací.
Basic autentizace vzniká až uvnitř jednoho requestu a nikdy se neloguje.

Veřejné stránkování nástroje `list_tasks` je 1-based a runtime ho bezpečně
překládá na providerův 0-based parametr `p`. Filtry jsou omezené na projekt
a hledání v názvu úkolu. Detail vrací nejvýše 100 nejnovějších komentářů.
Žádné úkoly, komentáře, projekty ani time tracking se nemění.

Implementace vychází z oficiální OpenAPI specifikace Freelo a oficiálního
CLI. Mock testy nenahrazují přejímku skutečného účtu.

## Nástroje

| nástroj | popis |
|---|---|
| `list_projects` | Seznam vlastních aktivních projektů a tasklistů. |
| `get_project` | Detail projektu včetně dostupných tasklistů a rozpočtu. |
| `list_tasks` | Stránka úkolů s filtrem projektu nebo názvu. |
| `get_task` | Detail úkolu s omezeným počtem nejnovějších komentářů. |

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"email": "…", "api_key": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_freelo local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/freelo.json \
  python -m connector_freelo local call --privacy balanced list_projects
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_freelo mcp`
nebo `docker run -i … openmcp-connector-freelo mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py freelo --credentials $HOME/.openmcp/freelo.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
