# FinStat — read-only MCP konektor

Adaptér publikuje sedm čtecích nástrojů nad slovenským registrem firem FinStat
(`https://www.finstat.sk/api`). Žádný nástroj nic nezapisuje, monitoring ani
portfolio se nemění.

## Nástroje

| Nástroj | Provider endpoint | Poznámka |
|---|---|---|
| `get_basic` | `POST /api/basic.json` | základní údaje firmy podle IČO |
| `get_detail` | `POST /api/detail.json` | PREMIUM detail |
| `get_extended` | `POST /api/extended.json` | ELITE detail |
| `get_ultimate` | `POST /api/ultimate.json` | ULTIMATE detail |
| `autocomplete` | `POST /api/autocomplete.json` | našeptávač podle názvu / IČO |
| `list_statements` | `POST /api/GetStatements.json` | seznam účetních závěrek |
| `get_statement` | `POST /api/GetStatementDetail.json` | detail závěrky (rok + šablona) |

Safe-test volá `autocomplete` s dotazem `finstat` (nejlevnější autentizované volání).

## Credentials a autentizace

Klíče `api_key`, `private_key` a povinný `pii_key` přicházejí pouze v podepsaném
body svázaném s instalací. FinStat nemá GET rozhraní: každé volání je HTTP POST
s `application/x-www-form-urlencoded` poli `apiKey`, `Hash`, `StationId`,
`StationName` a argumentem metody; přípona `.json` vybírá JSON výstup. Ověřovací
hash se počítá přesně podle oficiálních klientů:
`sha256("SomeSalt+{apiKey}+{privateKey}++{parameter}+ended")`, kde `parameter`
je IČO, dotaz našeptávače, nebo `ico|rok` pro detail závěrky. `private_key`
nikdy neopouští runtime, posílá se jen odvozený hash. Vše prochází
`private_envelope`, takže osobní a neznámá pole jsou pseudonymizována.

## Egress a limity

Pevný origin `https://www.finstat.sk/api`, port 443, pouze metoda POST a pouze
sedm výše uvedených cest (runtime jiné cesty odmítne, testy to kontrolují).
Odpověď je omezena na 2 MiB; FinStat vrací denní/měsíční limity v hlavičkách
`finstat-*-limit-*`, po vyčerpání odpovídá HTTP 402 (adaptér vrací bezpečnou
chybu `upstream_error`). HTTP 403 (neplatný klíč nebo hash, vypršelá licence)
se mapuje na `credential_invalid`, 451 (GDPR omezení) na `upstream_error`.

## Ověřeno podle

- https://www.finstat.sk/api (přehled API, formáty JSON/XML, odkaz na klienty)
- https://github.com/finstat/ClientApi.PHP (`FinStat.Client/AbstractFinstatApi.php`,
  `FinStatApi/FinstatApi.php`, `FinStatApi/FinstatStatementApi.php`,
  `FinStat.Client/BaseFinstatApi.php`) — POST form pole, `ComputeVerificationHash`,
  cesty `basic|detail|extended|ultimate|autocomplete|GetStatements|GetStatementDetail`
- https://github.com/finstat/ClientApi.CSharp (`Shared/FinStatApi.Client/*.cs`) — shodný hash a form POST

Podrobná HTML dokumentace FinStat je dostupná až po přihlášení; endpointy, které
nešlo ověřit z oficiálních klientů (monitoring, exekuce, denní diffy), adaptér
záměrně nepublikuje. Mock testy nenahrazují přejímku skutečného účtu.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"api_key": "…", "private_key": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_finstat local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/finstat.json \
  python -m connector_finstat local call --privacy balanced get_basic
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_finstat mcp`
nebo `docker run -i … openmcp-connector-finstat mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py finstat --credentials $HOME/.openmcp/finstat.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
