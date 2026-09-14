# Marketing Miner — read-only MCP konektor

Adaptér publikuje čtyři čtecí nástroje nad veřejným Profilers API
`https://profilers-api.marketingminer.com` (JSend odpovědi):

| Nástroj | Endpoint providera |
|---|---|
| `keyword_search_volume` | `GET /keywords/search-volume-data` (`lang`, `keyword`) |
| `keyword_suggestions` | `GET /keywords/suggestions` (`lang`, `keyword`, `suggestions_type`, `with_keyword_data`) |
| `website_stats` | `GET /websites/stats` (`lang`, `target`, `type`, `scheme`) |
| `website_stats_range` | `GET /websites/stats-range` (`lang`, `target`, `type`, `scheme`, `period`) |

Credentials: `api_token` (API token z profilu uživatele, placený plán) a povinný
`pii_key`. Provider autentizuje výhradně dokumentovaným query parametrem
`api_token`; SDK query string nikdy nezapisuje do provenance ani do chyb.
Egress je pevně `profilers-api.marketingminer.com:443`, pouze `GET`.

Limity: klíčové slovo 2–80 znaků, cíl (doména/URL) do 253 znaků bez mezer,
trhy `cs, sk, pl, hu, ro, gb, us`. Každý dotaz čerpá kredity účtu (3 kredity
za hledanost, 10 za návrhy i statistiky webu); `test_connection` proto provádí
jediný nejlevnější dotaz na hledanost. Odpovědi procházejí `private_envelope`,
neznámé textové hodnoty jsou pseudonymizovány. Nic se nezapisuje.

Ověřeno podle https://help.marketingminer.com/en/cat/for-developers/
(články Keyword Search Volume API, Keyword Suggestions API, Domain and URL
visibility statistics) a OpenAPI specifikace
https://profilers-api.marketingminer.com/openapi.json. Mock testy nenahrazují
přejímku skutečného účtu.

## Nástroje

| nástroj | popis |
|---|---|
| `keyword_search_volume` | Měsíční hledanost, CPC, sezónnost a obtížnost jednoho klíčového slova pro daný trh. |
| `keyword_suggestions` | Návrhy souvisejících klíčových slov (otázky, nová, trendová) k zadanému výrazu. |
| `website_stats` | Viditelnost domény nebo URL ve vyhledávání: odhad návštěvnosti a počet klíčových slov. |
| `website_stats_range` | Vývoj viditelnosti domény nebo URL v čase (denní, týdenní nebo měsíční řada). |

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"api_token": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_marketingminer local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/marketingminer.json \
  python -m connector_marketingminer local call --privacy balanced keyword_search_volume
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_marketingminer mcp`
nebo `docker run -i … openmcp-connector-marketingminer mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py marketingminer --credentials $HOME/.openmcp/marketingminer.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
