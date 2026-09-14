# GoPay — read-only MCP konektor

Adaptér publikuje čtyři čtecí nástroje nad GoPay REST API (`/api`, dokumentace
doc.gopay.com). Provider credentials `goid`, `client_id`, `client_secret`,
`environment` a povinný `pii_key` přicházejí pouze v podepsaném body svázaném
s instalací. `environment` vybírá jednu ze dvou pevně daných bran; žádná URL
z credentials ani z argumentů se nepřijímá:

| `environment` | origin |
|---|---|
| `production` | `https://gate.gopay.cz` |
| `sandbox` | `https://gw.sandbox.gopay.com` |

## Autentizace

Každé volání nejprve vymění `client_id:client_secret` (HTTP Basic) za krátkodobý
Bearer token přes `POST /api/oauth2/token` (`grant_type=client_credentials`,
`scope=payment-all`, tělo `application/x-www-form-urlencoded`) a poté provede
právě jeden dokumentovaný GET. Token se neukládá ani neloguje; po každém
volání zaniká spolu s klientem. GoPay má jen dva scopy: `payment-create` smí
pouze zakládat platby, `payment-all` je jediný, kterým lze platby dotazovat.
Token tedy technicky opravňuje i k zápisu, adaptér ale nikdy nepošle nic jiného
než token POST a dokumentované GETy — obal transportu odmítne jakoukoli jinou
metodu či cestu ještě před odesláním (testováno pro `POST /payments/payment`,
`.../refund`, `/accounts/account-statement`, `DELETE /payments/cards/{id}`).

## Nástroje

| nástroj | endpoint |
|---|---|
| `get_payment` | `GET /api/payments/payment/{id}` |
| `list_refunds` | `GET /api/payments/payment/{id}/refunds` |
| `get_card` | `GET /api/payments/cards/{card_id}` |
| `list_payment_instruments` | `GET /api/eshops/eshop/{goid}/payment-instruments` nebo `.../payment-instruments/{currency}` |

Safe-test získá token a zavolá `GET /api/eshops/eshop/{goid}/payment-instruments`.
`goid` pochází výhradně z credentials (nikdy z argumentů); `payment_id`/`card_id`
jsou kladná celá čísla, `currency` je uzavřený výčet GoPay (`CZK`, `EUR`, `PLN`,
`USD`, `GBP`, `HUF`, `RON`). Žádné zakládání plateb, refundace, capture/void,
opakované platby ani mazání karet se neprovádí; write nástroje neexistují.

Vynecháno: `POST /api/accounts/account-statement` (výpis účtu) je POST vracející
soubor (CSV/XLSX/ABO), nikoli JSON, proto není součástí allow-listu.
`GET /api/payments/payment/{id}/qr-payment` vrací obrázek. Novější GoPay
Payments API v4 (`api-docs.gopay.com`, base `/gp-gw/api/4.0`, scopy
`payment:read`) je jiné API s jinými cestami; tento adaptér cílí na REST API
popsané na doc.gopay.com, které používají stávající `goid`/`client_id`
credentials a oficiální SDK.

## Limity a PII

Upstream odpověď je omezena na 2 MiB, bezpečný výstup na 256 KiB; nad limitem
vrací adaptér chybu, nikoli zkrácená data. Neznámé texty a názvy polí jsou
pseudonymizované per uživatel/workspace/instalace/GoID (kontakt plátce,
maskované číslo karty, e-mail); jde o pseudonymizaci, ne anonymizaci ani
náhradu autorizace. Odpověď obsahující `errors` je mapována na bezpečnou
chybu bez textu providera. Archivované platby vrací GoPay jako
`PAYMENT_NOT_FOUND`; adaptér to hlásí jako chybu bez detailu.

## Ověřeno podle

- https://doc.gopay.com/ (REST API: sandbox/produkční endpointy, OAuth2 token
  `POST /api/oauth2/token` se scopy `payment-create`/`payment-all`, `GET
  /api/payments/payment/{id}`, `GET /api/payments/payment/{id}/refunds`, `GET
  /api/payments/cards/{card_id}`, `GET /api/eshops/eshop/{goid}/payment-instruments[/{currency}]`,
  výčet měn, tvar chyb `errors[]`)
- https://api-docs.gopay.com/ a https://api-docs.gopay.com/spec/en/payments.yaml
  (OpenAPI Payments API v4 — ověřeno, že jde o samostatné API s jiným base path
  a scopy; není použito)

Mock testy nenahrazují přejímku skutečného účtu.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"goid": "…", "client_id": "…", "client_secret": "…", "environment": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_gopay local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/gopay.json \
  python -m connector_gopay local call --privacy balanced get_payment
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_gopay mcp`
nebo `docker run -i … openmcp-connector-gopay mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py gopay --credentials $HOME/.openmcp/gopay.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
