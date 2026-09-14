# Packeta — read-only MCP konektor

Adaptér publikuje sedm čtecích nástrojů: sledování zásilek přes REST/XML API
Packeta a seznam dopravců z feedu v5. Nikdy nevytváří zásilky, štítky, svozy,
storna ani nevolá `packetCourierNumber` (předává zásilku externímu dopravci).

## Nástroje

| Nástroj | Provider endpoint / metoda |
|---|---|
| `packet_status` | `POST https://www.zasilkovna.cz/api/rest` — `<packetStatus>` |
| `packet_tracking` | `POST …/api/rest` — `<packetTracking>` |
| `packet_courier_tracking` | `POST …/api/rest` — `<packetCourierTracking>` |
| `packet_info` | `POST …/api/rest` — `<packetInfo>` |
| `packet_stored_until` | `POST …/api/rest` — `<packetGetStoredUntil>` |
| `shipment_packets` | `POST …/api/rest` — `<shipmentPackets>` |
| `list_carriers` | `GET https://pickup-point.api.packeta.com/v5/{apiKey}/carrier/json?lang=` |

REST API Packeta je pouze XML: kořenový element = název metody, první dítě
`apiPassword`. Runtime staví XML z uzavřeného seznamu šesti metod, cokoli jiného
odmítne ještě před odesláním (testy to kontrolují). XML odpověď se převádí na
JSON, `<status>fault</status>` se mapuje na bezpečnou chybu
(`IncorrectApiPasswordFault` → `credential_invalid`, `PacketIdFault` /
`ShipmentNotFoundFault` → `not_found`), text chyby providera se nepropaguje.
DOCTYPE/ENTITY v odpovědi je odmítnut, velikost XML je omezena na 1 MiB.

`list_carriers` stáhne feed dopravců (JSON, cca 1 MB) a lokálně filtruje podle
`country` a podstringu `name`; výstup je stránkován `offset`/`limit` (max 200).
Feedy poboček a Z-BOXů (15–16 MB) adaptér záměrně nenabízí — přesahují limity SDK.

## Credentials

`api_password` (REST API), `api_key` (feed v5; podle dokumentace se ve feedu
nikdy nepoužívá API heslo) a povinný `pii_key`. Přicházejí jen v podepsaném
body svázaném s instalací. Provenance feedu obsahuje placeholder `{apiKey}`,
skutečný klíč se nikdy neloguje. Safe-test volá feed dopravců (ověří `api_key`);
`api_password` ověří až první dotaz na zásilku, Packeta nemá samostatný auth check.

## Egress a limity

Hosty `www.zasilkovna.cz` (POST, jen `/api/rest`) a `pickup-point.api.packeta.com`
(GET, jen `/v5/{apiKey}/carrier/json`), port 443. Packeta synchronizuje stavy
externích dopravců 3× denně; feed je cachovaný, doporučené volání nejvýše 1× za
hodinu.

## Ověřeno podle

- https://docs.packeta.com/docs/getting-started/packeta-api (REST endpoint, XML tvar požadavku/odpovědi)
- https://docs.packeta.com/docs/api-reference/api-methods (`packetStatus`, `packetTracking`,
  `packetCourierTracking`, `packetInfo`, `packetGetStoredUntil`, `shipmentPackets`, deprekace
  `packetCourierNumber`)
- https://docs.packeta.com/docs/packet-tracking/tracking (příklady odpovědí)
- https://docs.packeta.com/docs/api-reference/errors (`IncorrectApiPasswordFault`, `PacketIdFault`, …)
- https://docs.packeta.com/docs/pudo-delivery/packeta-pudos a https://docs.packeta.com/how-to-update-packeta-feed
  (feed v5 `branch/json`, `box/json`, `carrier/json`, parametr `lang`, API key místo API hesla)

Mock testy nenahrazují přejímku skutečného účtu.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"api_password": "…", "api_key": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_packeta local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/packeta.json \
  python -m connector_packeta local call --privacy balanced packet_status
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_packeta mcp`
nebo `docker run -i … openmcp-connector-packeta mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py packeta --credentials $HOME/.openmcp/packeta.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
