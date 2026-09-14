# Comgate — read-only MCP konektor

Adaptér publikuje čtyři čtecí nástroje nad Comgate REST API v2.0
(`https://payments.comgate.cz/v2.0`, JSON reprezentace přes příponu `.json`).
Provider credentials `merchant`, `secret` a povinný `pii_key` přicházejí pouze
v podepsaném body svázaném s instalací. HTTP Basic autentizace
`merchant:secret` vzniká až uvnitř jednoho requestu a nikdy se neloguje.
Port 443, HTTP, redirecty a proxy z environmentu jsou odmítnuté; žádná URL
z credentials ani z argumentů se nepřijímá.

## Nástroje (vše GET)

| nástroj | endpoint |
|---|---|
| `get_payment` | `GET /v2.0/payment/transId/{transId}.json` |
| `list_transfers` | `GET /v2.0/transferList/date/{YYYY-MM-DD}.json` (`test=true` volitelně) |
| `get_transfer` | `GET /v2.0/singleTransfer/transferId/{transferId}.json` (`test=true` volitelně) |
| `list_methods` | `GET /v2.0/method.json` (`lang`, `curr`, `country` volitelně) |

Safe-test volá `GET /v2.0/method.json`. `trans_id` má pevný tvar
`AAAA-BBBB-CCCC`, `date` je kalendářní datum (Europe/Prague dle dokumentace),
`transfer_id` kladné celé číslo; `lang`/`currency`/`country` jsou uzavřené
výčty z dokumentace. Odpověď s `code` jiným než `0` (1100–1500) je mapována na
bezpečnou chybu bez textu providera. Žádné zakládání, rušení, refundace,
opakované platby ani capture/cancel předautorizace se neprovádí; write
nástroje neexistují.

Vynecháno: `csvDownload`/`aboDownload` (ZIP soubory) a
`csvSingleTransfer`/`aboSingleTransfer` (base64 soubory v JSON) nejsou JSON
data, `appleDomainAssociation` a `config` nejsou čtení obchodních dat.
Dokumentace neuvádí žádný endpoint pro seznam plateb podle dne/filtru; platby
lze dotazovat jen po jedné podle `transId`, výplaty podle dne přes
`transferList`. Legacy HTTP POST API (`payments.comgate.cz/v1.0/`) není použito.

## Limity a PII

Upstream odpověď je omezena na 2 MiB, bezpečný výstup na 256 KiB; nad limitem
vrací adaptér chybu, nikoli zkrácená data. Neznámé texty a názvy polí jsou
pseudonymizované per uživatel/workspace/instalace/merchant (e-mail a jméno
plátce, číslo účtu, variabilní symboly); jde o pseudonymizaci, ne anonymizaci
ani náhradu autorizace. Comgate chrání API validací IP adresy — egress IP
platformy musí být povolená v Klientském portálu.

## Ověřeno podle

- https://apidoc.comgate.cz/api/rest/ (REST API v2.0: base
  `https://payments.comgate.cz/v2.0`, hlavička `Authorization: Basic
  base64(merchant:secret)`, `GET /payment/transId/{transId}.json`, `GET
  /transferList/date/{date}.json`, `GET /singleTransfer/transferId/{transferId}.json`,
  `GET /method.json` s parametry `lang`/`curr`/`country`, návratové kódy
  `code`/`message`, seznam write operací)
- https://apidoc.comgate.cz/uvod/ (rozcestník: REST API vs. HTTP POST API)

Mock testy nenahrazují přejímku skutečného účtu.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"merchant": "…", "secret": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_comgate local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/comgate.json \
  python -m connector_comgate local call --privacy balanced get_payment
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_comgate mcp`
nebo `docker run -i … openmcp-connector-comgate mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py comgate --credentials $HOME/.openmcp/comgate.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
