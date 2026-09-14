# Heureka Marketplace — read-only MCP konektor

Adaptér publikuje tři čtecí nástroje nad „API Heureka“ částí protokolu
Heureka Marketplace (`/api/cart/{api_key}/1/...`). Provider credentials
`api_key`, `country` a povinný `pii_key` přicházejí pouze v podepsaném body
svázaném s instalací. `country` vybírá jeden ze dvou pevně daných hostů; žádná
URL z credentials ani z argumentů se nepřijímá:

| `country` | origin |
|---|---|
| `cz` | `https://ssl.heureka.cz` |
| `sk` | `https://ssl.heureka.sk` |

API klíč z Partner portálu je podle dokumentace segmentem cesty každého
volání (žádná hlavička). Adaptér ho vkládá URL-escapovaný až uvnitř jednoho
requestu; provenance (`source_url`) nese místo klíče zástupný text
`{api_key}` a klíč se nikdy neloguje ani nevrací.

## Nástroje (vše GET)

| nástroj | endpoint |
|---|---|
| `get_order_status` | `GET /api/cart/{api_key}/1/order/status?order_id=…` |
| `list_stores` | `GET /api/cart/{api_key}/1/stores` |
| `get_shop_status` | `GET /api/cart/{api_key}/1/shop/status` |

Safe-test volá `GET .../1/shop/status` (Heureka jej cachuje 30 minut).
`order_id` je kladné celé číslo. Operace jsou pevný allow-list; `PUT
order/status`, `PUT payment/status`, `PUT order/cancel`, `POST order/note`,
`POST order/invoice` ani žádné potvrzení, storno či expedice objednávky se
nikdy neprovádí a test ověřuje, že jsou odmítnuty před odesláním.

## Co dokumentace nenabízí (vynecháno)

Protokol Marketplace je dvoustranný: seznam objednávek, položky objednávek,
dostupnost produktů a možnosti dopravy Heureka **posílá do API obchodu**
(`POST order/send`, `GET products/availability`, `GET payment/delivery` na
straně e-shopu). Na straně Heureky žádný endpoint pro seznam objednávek,
detail/položky objednávky, historii stavů ani katalog produktů dokumentován
není, proto tyto nástroje neexistují; stav jedné objednávky se dotazuje podle
`order_id`. `POST /api/cart/{api_key}/1/payout-report` (výplatní report) vrací
`text/csv` s jazykově závislými sloupci, nikoli JSON, a není součástí
allow-listu. Base `api.heureka.cz/marketplace/v1` ani hlavičky `X-Api-Key`/
`Heureka-Api-Key` v dokumentaci neexistují.

## Limity a PII

Upstream odpověď je omezena na 2 MiB, bezpečný výstup na 256 KiB; nad limitem
vrací adaptér chybu, nikoli zkrácená data. Neznámé texty a názvy polí jsou
pseudonymizované per uživatel/workspace/instalace/host (názvy poboček,
interní čísla objednávek, chybové zprávy); jde o pseudonymizaci, ne
anonymizaci ani náhradu autorizace.

## Ověřeno podle

- https://heureka.github.io/marketplace-api/ a
  https://heureka.github.io/marketplace-api/swagger.yaml (OpenAPI: servery
  `https://ssl.heureka.cz/` a `https://ssl.heureka.sk/`, `apiKey` jako path
  parametr, egress volání na straně partnera, `payout-report` jako `text/csv`)
- https://sluzby.heureka.cz/napoveda/marketplace-api/ (technická specifikace:
  `GET order/status` s `order_id`, `GET stores`, `GET shop/status`, write
  metody `PUT order/status`, `PUT payment/status`, `POST order/note`,
  `POST order/invoice`, `PUT order/cancel`, tvar odpovědí)

Mock testy nenahrazují přejímku skutečného účtu.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"api_key": "…", "country": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_heureka local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/heureka.json \
  python -m connector_heureka local call --privacy balanced get_order_status
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_heureka mcp`
nebo `docker run -i … openmcp-connector-heureka mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py heureka --credentials $HOME/.openmcp/heureka.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
