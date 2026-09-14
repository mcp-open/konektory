# Reservio — read-only MCP konektor

Adaptér publikuje osm čtecích nástrojů nad Reservio API v2
(`https://api.reservio.com/v2`, JSON:API). Výchozí interní port je 8134.

## Nástroje

| Nástroj | Endpoint (vždy `GET`, vždy pod `/businesses/{businessId}`) |
| --- | --- |
| `get_business` | `/businesses/{businessId}` |
| `list_services` | `/businesses/{businessId}/services` |
| `get_service` | `/businesses/{businessId}/services/{serviceId}` |
| `list_resources` | `/businesses/{businessId}/resources` (zaměstnanci, místnosti) |
| `list_opening_hours` | `/businesses/{businessId}/opening-hours` |
| `booking_slots` | `/businesses/{businessId}/availability/booking-slots?filter[from]&filter[to]&filter[serviceId]&filter[resourceId]` |
| `list_events` | `/businesses/{businessId}/events?sort=±createdAt` |
| `list_bookings` | `/businesses/{businessId}/bookings?sort=±createdAt` |

Safe-test volá dokumentovaný ověřovací endpoint `/users/me`. Žádné
`POST /bookings` (vytvoření rezervace), rušení rezervací ani jiné zápisy
nejsou dostupné; adaptér posílá výhradně `GET` a `businessId` bere jen
z credentials, nikdy z argumentů.

Interval `booking_slots` je povinný a ohraničený na 3 měsíce. Odpověď
seznamu obsahuje `data`, `count`, `total` (z `meta.total`, je-li přítomné) a
`truncated` (true, pokud provider vrátil `links.next`).

## Co veřejná dokumentace neumožňuje ověřit

Veřejný API Blueprint dokumentuje stránkování pouze jako odkazy
`links.first/prev/next/last` v odpovědi a filtrování jen obecně
(`?filter[name]=…`, `?filter[booking.event.id]=…`); názvy parametrů pro
číslo/velikost stránky ani datový filtr událostí a rezervací nejsou
dokumentované. Adaptér proto u `list_events`/`list_bookings` nabízí jen
dokumentované řazení `sort=-createdAt` (doporučené i pro polling) a
neodhaduje `page[...]`/`filter[start]` parametry; velký seznam skončí
chybou limitu výstupu (256 KiB), nikdy tichým zkrácením. Endpointy klientů
(zákazníků) veřejná dokumentace neobsahuje, proto chybí. Vynechané jsou i
`enterprises`, `business-groups`, `coupons`, `rosters`, `booking-days`,
`event-days` a `availability/report` (jediná dokumentovaná hodnota `type`
je `hourly`), které lze doplnit stejným vzorem.

## Credentials a egress

Credentials přicházejí pouze v podepsaném body svázaném s instalací:
`access_token` (Bearer token – zákaznický token vydaný podporou Reservio
v plánu Pro, nebo partnerský `access_token` z `POST /oauth2/token`
`client_credentials`; adaptér token sám nezískává ani neobnovuje),
`business_id` (UUID podniku) a nejméně 32bytový `pii_key`. Token se posílá
výhradně v hlavičce `Authorization: Bearer …` spolu s
`Accept: application/vnd.api+json`; nikdy v URL. Egress: GET na
`api.reservio.com:443`, prefix `/v2`.

## Limity a ochrana dat

Upstream odpověď max. 2 MiB, výsledek max. 256 KiB, jinak chyba. Neznámá
pole a texty (jména klientů, e-maily, telefony, poznámky, názvy služeb,
UUID) jsou pseudonymizované přes `private_envelope`; čitelné zůstávají jen
vybraná číselná/datová/boolean pole (`id` jen číselné, `count`, `total`,
`truncated`, `type`, …). Chyby providera (400/401/403/404/429/5xx, tělo s
`errors`, chybějící `data`, nevalidní JSON) se mapují na `ConnectorError`
bez textu providera; 429 je `rate_limited` (retryable, GET se opakuje
nejvýše dvakrát).

Ověřeno podle oficiální dokumentace Reservio API v2
(https://www.reservio.com/developers → API Blueprint
https://reservioapiv2.docs.apiary.io/ — Authentication (OAuth2), Testing
your token, Sorting/filtering/pagination, Webhooks & integrations a zdroje
Business, Service Collection/Service, Resource Collection, Opening Hours
Collection, Booking Slots, Event Collection, Booking Collection). Mock testy
nenahrazují přejímku skutečného účtu.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"access_token": "…", "business_id": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_reservio local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/reservio.json \
  python -m connector_reservio local call --privacy balanced get_business
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_reservio mcp`
nebo `docker run -i … openmcp-connector-reservio mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py reservio --credentials $HOME/.openmcp/reservio.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
