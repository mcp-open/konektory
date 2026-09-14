# RAYNET CRM — read-only MCP konektor

Šestnáct čtecích nástrojů nad RAYNET CRM API v2 (`https://app.raynet.cz/api/v2`):
klienti, kontakty, obchodní případy, leady, aktivity, produkty a číselníky.
Credentials `instance_name`, `username`, `api_key` a povinný `pii_key`; žádné
zápisy do CRM.

## Nástroje

| nástroj | popis |
|---|---|
| `raynet_whoami` | Informace o připojeném provider účtu s povinnou PII ochranou. |
| `raynet_resources` | Pevný seznam podporovaných zdrojů a číselníků adaptéru. |
| `search_companies` | Stránka klientů filtrovaná podle jména, IČO a vlastníka. |
| `get_company` | Detail klienta podle ID. |
| `get_company_by_ext` | Detail klienta podle externího ID. |
| `search_persons` | Stránka kontaktních osob; kontaktní údaje jsou pseudonymizované. |
| `get_person` | Detail kontaktní osoby podle ID. |
| `get_person_by_ext` | Detail kontaktní osoby podle externího ID. |
| `search_business_cases` | Stránka obchodních případů podle klienta a stavu. |
| `get_business_case` | Detail obchodního případu podle ID. |
| `search_leads` | Stránka leadů podle jména, stavu a vlastníka. |
| `get_lead` | Detail leadu podle ID. |
| `raynet_list` | Jedna stránka podporovaného zdroje, jen pevně povolené filtry. |
| `raynet_get` | Detail podporovaného zdroje podle ID. |
| `raynet_get_by_ext` | Detail podporovaného zdroje podle externího ID. |
| `raynet_codebook` | Jedna stránka podporovaného číselníku. |

## Přenesené nástroje

Šestnáct read-only názvů:

- `raynet_whoami`, `raynet_resources`;
- `search_companies`, `get_company`, `get_company_by_ext`;
- `search_persons`, `get_person`, `get_person_by_ext`;
- `search_business_cases`, `get_business_case`;
- `search_leads`, `get_lead`;
- `raynet_list`, `raynet_get`, `raynet_get_by_ext`, `raynet_codebook`.

Generické zdroje jsou pevně omezené na `company`, `person`, `lead`,
`businessCase`, `offer`, `salesOrder`, `project`, `product`. Každý zdroj má
vlastní uzavřený seznam filtrů; `filters` není libovolný provider query objekt
ani způsob přepsat stránkování. Není podporován raw URL, libovolná cesta,
libovolná hlavička nebo změna regionu.

Číselníky: `companyCategory`, `personCategory`, `businessCaseCategory`,
`businessCasePhase`, `businessCaseType`, `leadCategory`, `leadPhase`,
`currency`, `taxRate`, `productCategory`, `productLine`, `offerCategory`,
`offerStatus`, `salesOrderCategory`, `salesOrderStatus`, `projectStatus`.
Aktuální katalog vrací `raynet_resources`; není to důkaz práv živé instance.

## Bezpečnost a data

Pouze podepsané body s přesným `secret_ref=raynet/<workspace_id>/<installation_id>`
a platnou secret verzí smí přenést `instance_name`, `username`, `api_key`,
`pii_key`. Chybějící/krátký PII klíč odmítá invocation před provider voláním.
Podpis a SDK replay ochrana vážou body, tenant, instalaci, tool i krátké TTL.
Runtime nemá vlastní OpenBao přístup ani env fallback pro provider účet.

HTTP míří výhradně na `https://app.raynet.cz/api/v2`; Basic a validovaná
hlavička `X-Instance-Name` vznikají ze stejného body. Klient je per-call,
TLS se ověřuje, redirecty a env proxy jsou zakázané. Přijímá nejvýše 1 MiB
response a pseudonymizovaný výstup nejvýše 256 KiB. Provider exception text,
API klíče, instance ani hledané query hodnoty se nevracejí do provenance.

Jména, kontakty, volný text a neznámá pole se konzervativně pseudonymizují;
neexistuje přepínač `redact_pii=false`. HMAC je vázaný na uživatele,
workspace, instalaci i RAYNET instanci. Nejde o anonymizaci. Výstup je
označen jako nedůvěryhodná externí data, nikoli instrukce. Čitelnost
některých katalogových/názvových polí je záměrně omezená.

List/search nástroje mají `limit` 1–100 a `offset` 0–10000; neplatné hodnoty
se neclampují potichu. Číselníky mají nově rovněž nejvýše 100 položek
(výchozí limit 100). Vrací se celá přijatá stránka včetně vnějšího
`totalCount`, nikoli tichý výřez záznamů. Neznámá/chybná success/data obálka
neznamená prázdný úspěch. `test_connection` volá jen GET `/security/info`;
potvrzuje připojení, ne všechna oprávnění jednotlivých evidencí.

## Odložené části historie

Není přeneseno těchto devět analytických nástrojů: `sales_pipeline`,
`owner_performance`, `lead_funnel`, `portfolio_summary`, `account_overview`,
`stale_business_cases`, `product_mix`, `pipeline_trend`, `cross_sell`.
Potřebují samostatnou přejímku agregací, N+1 limitů a přesnosti metrik.

Čtyři historické prompty nejsou registrované: `report_pipeline`,
`zdravi_klienta`, `zaseknute_obchody`, `cross_sell_tipy`.

Sedm write nástrojů není registrováno ani skrytě zapnutelné flagem:
`create_task`, `create_phone_call`, `create_meeting`, `create_lead`,
`update_business_case`, `update_company`, `update_person`. Rovněž nejsou
mazání, anonymize/merge, lock/unlock, convert, upload ani hromadné operace.

Oproti starému registru je odloženo deset zdrojů: `task`, `activity`,
`priceList`, `invoiceLight`, `email`, `phoneCall`, `meeting`, `event`,
`letter`, `userAccount`. Podporovány jsou jen zde vyjmenované číselníky;
ostatní historické číselníky ani jiné regiony nejsou aktivované.

## Ověření a další integrace

WSL Docker `make test-python` ověřuje lint/types, provider mapování,
negativní vstupy, credential scope, malformed responses, pseudonymizaci
a zachování stránek na syntetickém `httpx.MockTransport`. Kořenové testy
procházejí skutečnou podepsanou HTTP/replay hranicí. Není to přejímka
skutečného CRM účtu, práv konkrétního uživatele ani všech provider filtrů.

Platformní katalog, provisioning skutečných credentials, lock update,
integrační release a nasazení jsou další samostatná dodávka.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"instance_name": "…", "username": "…", "api_key": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_raynet local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/raynet.json \
  python -m connector_raynet local call --privacy balanced raynet_whoami
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_raynet mcp`
nebo `docker run -i … openmcp-connector-raynet mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py raynet --credentials $HOME/.openmcp/raynet.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
