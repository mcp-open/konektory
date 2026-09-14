# Websupport — read-only MCP konektor

Adaptér publikuje sedm čtecích nástrojů nad `https://rest.websupport.sk`
(služby, DNS zóny a záznamy, FTP účty). Nic se nevytváří, nemění ani nemaže.

## Nástroje

| Nástroj | Provider endpoint |
|---|---|
| `list_services` | `GET /v1/user/self/service?page&pagesize` |
| `get_service` | `GET /v1/user/self/service/{serviceId}` |
| `list_zones` | `GET /v1/user/self/zone?page&pagesize` |
| `get_dns_zone` | `GET /v2/service/{service}/dns/zone` |
| `list_dns_records` | `GET /v2/service/{service}/dns/record?page&rowsPerPage[&filters[name]&filters[content]]` |
| `list_ftp_accounts` | `GET /v2/service/{service}/ftp-account?page&rowsPerPage` |
| `get_ftp_account` | `GET /v2/service/{service}/ftp-account/{ftpAccount}` |

`{service}` je číselné ID služby (viz `list_services`). FTP schéma providera
neobsahuje hesla, adaptér je ani nikde nedoplňuje. Safe-test volá
`GET /v2/check` a vyžaduje `{"verified": true}`.

## Credentials a podpis

`api_key`, `api_secret` a povinný `pii_key` přicházejí pouze v podepsaném body
svázaném s instalací. Každý request se podepisuje přesně podle dokumentace:
kanonický řetězec `"{METHOD} {path} {unix timestamp}"` (cesta bez query, jako v
oficiálních PHP/Python/Swagger příkladech), podpis = hex HMAC-SHA1 se secretem,
hlavička `Authorization: Basic base64(apiKey:signature)` a stejný čas v
`X-Date` (v2) i `Date` (v1) ve tvaru ISO 8601 basic UTC (`20190123T104657Z`).
`api_secret` nikdy neopouští runtime. Každá odpověď prochází `private_envelope`.

## Egress a limity

Pevný host `rest.websupport.sk`, port 443, pouze GET, cesty jen pod
`/v1/user/self` a `/v2` (runtime jiné cesty odmítne). Stránkování je omezené na
max 200 řádků a 10 000 stránek; odpověď na 2 MiB. Chybové obálky providera
(`{"code","message"}`) se nikdy nepropagují, mapují se na `upstream_error`.

## Ověřeno podle

- https://rest.websupport.sk/v2/docs/intro a https://rest.websupport.sk/docs/v1.intro.md
  (HMAC-SHA1 podpis, Basic auth, `X-Date`/`Date`, formát chyb)
- https://rest.websupport.sk/v2/docs/openapi.json (`/v2/check`, `/v2/service/{service}/dns/zone`,
  `/v2/service/{service}/dns/record` s `page`, `rowsPerPage`, `filters`, `/v2/service/{service}/ftp-account`,
  `/v2/service/{service}/ftp-account/{ftpAccount}`)
- https://rest.websupport.sk/docs/v1.service.md a https://rest.websupport.sk/docs/v1.zone.md
  (`/v1/user/self/service`, `/v1/user/self/zone`, `page`/`pagesize`)
- https://www.websupport.sk/podpora/kb/rest-api/ (odkaz na aktuální dokumentaci)

Mock testy nenahrazují přejímku skutečného účtu.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"api_key": "…", "api_secret": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_websupport local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/websupport.json \
  python -m connector_websupport local call --privacy balanced list_services
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_websupport mcp`
nebo `docker run -i … openmcp-connector-websupport mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py websupport --credentials $HOME/.openmcp/websupport.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
