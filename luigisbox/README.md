# Luigi's Box — read-only MCP konektor

Adaptér publikuje šest čtecích nástrojů nad živými API Luigi's Box
(`https://live.luigisbox.com`). Výchozí interní port je 8129.

## Nástroje

| Nástroj | Endpoint |
| --- | --- |
| `search` | `GET /search` (`q`, `size` 1–200, `page`, `f[]`, `f_must[]`, `sort`, `facets`, `quicksearch_types`, `hit_fields`, `use_fixits`) |
| `autocomplete` | `GET /autocomplete/v2` (`q`, `type` ve tvaru `typ:počet`, `hit_fields`) |
| `top_items` | `GET /v1/top_items` (`type`, `hit_fields`) |
| `trending_queries` | `GET /v2/trending_queries` |
| `recommend` | `POST /v1/recommend` (jeden blok: `recommendation_type`, `item_ids` max. 10, `size` 1–50, `hit_fields`, `recommender_client_identifier`) |
| `content_export` | `GET /v1/content_export` (`size` 1–500, `hit_fields`, `requested_types`; HMAC podpis) |

Safe-test volá `GET /v2/trending_queries` (ověří `tracker_id`) a podepsaný
`GET /v1/content_export?size=1` (ověří pár `public_key`/`private_key`).

Veřejná API (`/search`, `/autocomplete/v2`, `/v1/top_items`,
`/v2/trending_queries`, `/v1/recommend`) identifikují web pouze parametrem
`tracker_id`, který adaptér vždy doplní sám. Recommender API je jediná
POST operace: přijímá výhradně JSON pole bloků, proto adaptér obaluje SDK
klienta transportem `ArrayBodyTransport`, který tělo `{"blocks": [...]}`
přepíše na dokumentované pole. POST je povolen jen na přesnou cestu
`/v1/recommend` (allow-list `ALLOWED_POST_PATHS`); testy ověřují, že žádná
jiná cesta ani metoda (`/v1/content`, `PATCH`, `DELETE`) se nikdy nevolá.

`content_export` je privátní API s HMAC-SHA256 podpisem přesně podle
„API principles“: hlavičky `Date` (HTTP datum), `Content-Type:
application/json; charset=utf-8` a `Authorization: ApiAuth
{public_key}:{Base64(HMAC-SHA256(private_key, "GET\n{Content-Type}\n{Date}\n/v1/content_export"))}`.
Vrací se pouze první stránka exportu; odkaz `links[rel=next]` je opaque URL a
adaptér ho nenásleduje (žádná URL z dat ani z argumentů se nikdy nevolá).

## Credentials a egress

Credentials přicházejí pouze v podepsaném body svázaném s instalací:
`tracker_id` (identifikátor webu), `public_key` (veřejný klíč pro `ApiAuth`,
v Luigi's Box shodný s tracker ID), `private_key` (tajný klíč pro podpis) a
nejméně 32bytový `pii_key`. `private_key` se nikdy neposílá, slouží jen
k výpočtu podpisu. Egress: GET a POST na `live.luigisbox.com:443`.

## Limity a ochrana dat

Provider omezuje vyhledávání na 350 req/min, našeptávač 800 req/min a
recommender 60 req/5 s na tracker; 429 se mapuje na `rate_limited`
(retryable, čtecí operace se opakují nejvýše dvakrát). Upstream odpověď
max. 2 MiB, výsledek max. 256 KiB, jinak chyba (nikdy tiché zkrácení).
Neznámá pole a texty (názvy produktů, atributy, hledané fráze) jsou
pseudonymizované přes `private_envelope`; čitelné zůstávají jen vybraná
číselná/datová/boolean pole. Chyby providera (400/401/403/404/429/5xx, tělo
s `error`/`errors`, nevalidní JSON) se mapují na `ConnectorError` bez textu
providera.

Vynecháno: Reporting API (`analytics.luigisbox.com`) je podle dokumentace
deprecated a určené k odstranění; Analytics Events API (`api.luigisbox.com`)
je zápisové (ingest událostí); Content Updates API je zápisové. Parametry
`user_id`/`client_id` (personalizace) adaptér záměrně neposílá.

Ověřeno podle oficiální dokumentace Luigi's Box
(https://docs.luigisbox.com/platform-foundations/api-principles/,
https://docs.luigisbox.com/search/api/v1/search/,
https://docs.luigisbox.com/autocomplete/api/v2/autocomplete/,
https://docs.luigisbox.com/autocomplete/api/v1/top-items/,
https://docs.luigisbox.com/autocomplete/api/v2/trending-queries/,
https://docs.luigisbox.com/recommendations/api/v1/recommender/,
https://docs.luigisbox.com/indexing/api/v1/export/,
https://docs.luigisbox.com/analytics/api/reporting/). Mock testy nenahrazují
přejímku se skutečným účtem.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"tracker_id": "…", "public_key": "…", "private_key": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_luigisbox local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/luigisbox.json \
  python -m connector_luigisbox local call --privacy balanced search
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_luigisbox mcp`
nebo `docker run -i … openmcp-connector-luigisbox mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py luigisbox --credentials $HOME/.openmcp/luigisbox.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
