# Collabim — read-only MCP konektor

Adaptér publikuje osm čtecích nástrojů nad Collabim API
(`https://api.oncollabim.com`). Výchozí interní port je 8132.

## Nástroje

| Nástroj | Endpoint (vždy `GET`) |
| --- | --- |
| `list_projects` | `/projects` (`nameLike`, `active`, `page`, `itemsPerPage`) |
| `get_project` | `/projects/{projectId}` |
| `list_keywords` | `/keywords` (`projectId`, `keywordLike`, `tags`, `starred`, `page`, `itemsPerPage`) |
| `keyword_positions` | `/keyword-positions` (`projectId`, `from`, `to`, `projectKeywordIds` nebo `tags`, `getXDays`) |
| `aggregated_positions` | `/aggregated-keywords-positions` (`projectId`, `from`, `to`, `tags`, `getXDays`) |
| `position_distribution` | `/position-distribution` (`projectId`, `from`, `to`, `tagName`, `getXDays`) |
| `market_share` | `/market-share` (`projectId`, `searchEngineId`, `from`, `to`, `tagName`, `getXDays`) — konkurenční domény |
| `list_activities` | `/activities` (`projectId`, `addedOnFrom`/`addedOnTo`, `typeId`, `stateId`, `page`, `itemsPerPage`) |

Safe-test volá `/projects?page=1&itemsPerPage=1`. Žádné `PUT`/`DELETE`
(aktivity), `POST` (registrace, one-time analýzy, AI analýzy) ani stahování
CSV/XLSX nejsou dostupné; adaptér posílá výhradně `GET` na těchto osm
dokumentovaných cest. Endpointy Holy Grail, Google AI Overviews, widgetů a
`/website-system` jsou vynechané (placené jednorázové analýzy nebo
nestrukturované výstupy), `/keyword-positions` s `lastXDays` je nahrazen
ekvivalentní variantou `from`/`to`.

Stránkování je providerovo `page` (1-based) + `itemsPerPage` (1–100).
Datové intervaly jsou povinné pro nástroje pozic a podílu na trhu, ohraničené
na jeden rok (provider omezuje historii na 7000 klíčových slov × dnů; delší
interval odmítne sám). `keyword_positions` vyžaduje `project_keyword_ids`
(max. 200) nebo `tags`, přesně jak vyžaduje dokumentace. Odpověď seznamu
obsahuje `data` a `count`.

## Credentials a egress

Credentials přicházejí pouze v podepsaném body svázaném s instalací:
`api_key` (uživatelský API klíč z profilu; API funguje jen pro administrátora
nebo vlastníka účtu) a nejméně 32bytový `pii_key`. Klíč se posílá výhradně
v hlavičce `Authorization` (bez schématu, přesně podle dokumentace) spolu s
`Accept: application/collabim+json`; nikdy v URL. Egress: GET na
`api.oncollabim.com:443`, bez pevného prefixu (cesty jsou v kořeni).

## Limity a ochrana dat

Upstream odpověď max. 2 MiB, výsledek max. 256 KiB, jinak chyba (nikdy tiché
zkrácení). Neznámá pole a texty (názvy projektů, klíčová slova, URL, snippety,
štítky) jsou pseudonymizované přes `private_envelope`; čitelné zůstávají jen
vybraná číselná/datová/boolean pole (`id`, `count`, `date`, `active`, …).
Chyby providera (400/401/403/404/429/5xx, tělo s `errors`, chybějící `data`,
nevalidní JSON) se mapují na `ConnectorError` bez textu providera; 429 je
`rate_limited` (retryable, GET se opakuje nejvýše dvakrát).

Ověřeno podle oficiální dokumentace Collabim API
(https://help.collabim.com/tools/api a API Blueprint
https://collabimapi.docs.apiary.io/ — sekce Project info, Project list,
Keyword list, Keyword positions (from/to), Keyword aggregated positions,
Position distribution, Market share history a Activity list). Mock testy
nenahrazují přejímku skutečného účtu.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"api_key": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_collabim local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/collabim.json \
  python -m connector_collabim local call --privacy balanced list_projects
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_collabim mcp`
nebo `docker run -i … openmcp-connector-collabim mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py collabim --credentials $HOME/.openmcp/collabim.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
