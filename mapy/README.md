# Mapy.com — read-only MCP konektor

Adaptér publikuje pět čtecích nástrojů nad REST API `https://api.mapy.com/v1`:

| Nástroj | Endpoint providera |
|---|---|
| `geocode` | `GET /v1/geocode` (`query`, `lang`, `limit`, `type`, `locality`) |
| `reverse_geocode` | `GET /v1/rgeocode` (`lon`, `lat`, `lang`) |
| `suggest` | `GET /v1/suggest` (`query`, `lang`, `limit`, `type`, `locality`) |
| `route` | `GET /v1/routing/route` (`start`, `end`, `routeType`, `lang`, `format=polyline`, `avoidToll`, `avoidHighways`, `waypoints`) |
| `elevation` | `GET /v1/elevation` (`positions`, `lang`) |

Credentials: `api_key` (klíč z developer.mapy.com, posílá se výhradně v hlavičce
`X-Mapy-Api-Key`, nikdy v query) a povinný `pii_key`. Egress je pevně
`api.mapy.com:443` s prefixem `/v1`, pouze `GET`.

Limity: dotaz 1–150 znaků, `limit` 1–15, souřadnice jsou přísně číselné
(`lon` −180..180, `lat` −90..90, bez NaN/Inf a bez řetězců), nejvýše 15
průjezdních bodů trasy a 64 bodů pro výšku. Souřadnice se providerovi posílají
v dokumentovaném pořadí `lon,lat`. Trasa se vrací jako polyline, aby dlouhé
trasy nepřekročily velikost obálky. Odpovědi procházejí `private_envelope`,
neznámé hodnoty jsou pseudonymizovány. Nic se nezapisuje.

Ověřeno podle https://developer.mapy.com/rest-api-mapy-cz/ a OpenAPI
specifikací https://api.mapy.com/v1/docs/geocode/openapi.json,
https://api.mapy.com/v1/docs/routing/openapi.json a
https://api.mapy.com/v1/docs/elevation/openapi.json. Matrix routing, statické
mapy, dlaždice, panorama a časová pásma nejsou publikovány. Mock testy
nenahrazují přejímku skutečného klíče.

## Nástroje

| nástroj | popis |
|---|---|
| `geocode` | Geokódování: souřadnice a popis místa pro adresu, obec nebo název. |
| `reverse_geocode` | Zpětné geokódování: adresa a regiony pro souřadnice. |
| `suggest` | Našeptávač míst pro rozepsaný dotaz (nejvýše 15 návrhů). |
| `route` | Plánování trasy mezi dvěma body (auto, pěšky, kolo) s délkou, časem a polyline. |
| `elevation` | Nadmořská výška pro zadané souřadnice (nejvýše 64 bodů). |

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"api_key": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_mapy local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/mapy.json \
  python -m connector_mapy local call --privacy balanced geocode
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_mapy mcp`
nebo `docker run -i … openmcp-connector-mapy mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py mapy --credentials $HOME/.openmcp/mapy.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
