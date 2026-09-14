# iDoklad — read-only MCP konektor

Adaptér publikuje osm čtecích nástrojů nad iDoklad API v3
(`https://api.idoklad.cz/v3`). Provider credentials `client_id`, `client_secret`,
`application_id` a povinný `pii_key` přicházejí pouze v podepsaném body svázaném
s instalací.

## Autentizace (OAuth2 client credentials)

Podle aktuální dokumentace v3 a oficiálního SDK (Solitea/IdokladSdk) vyžaduje
client-credentials flow tři hodnoty: `client_id` a `client_secret` z nastavení
iDokladu (Nastavení – Aplikace/API) a `application_id` z Developer portálu.
Každá invokace provede **jeden** form POST na
`https://identity.idoklad.cz/server/v2/connect/token`
(`grant_type=client_credentials`, `application_id`, `client_id`, `client_secret`,
`scope=idoklad_api`) a získaný Bearer token použije pro jediný GET. Token se
nikde neukládá ani nesdílí mezi invokacemi; `offline_access`/refresh token se
nepoužívá. Token path je jediný POST, který adaptér umí odeslat — `FormTransport`
odmítne jakýkoli jiný požadavek na identity server a testy ověřují, že všechny
ostatní požadavky jsou GET na `api.idoklad.cz/v3/...`.

## Nástroje (vše GET)

| nástroj | endpoint |
|---|---|
| `list_issued_invoices` | `GET /v3/IssuedInvoices` (`filter`, `filtertype`, `sort`, `page`, `pagesize`) |
| `get_issued_invoice` | `GET /v3/IssuedInvoices/{id}` |
| `list_received_invoices` | `GET /v3/ReceivedInvoices` (`filter`, `filtertype`, `sort`, `page`, `pagesize`) |
| `list_contacts` | `GET /v3/Contacts` (`filter`, `filtertype`, `sort`, `page`, `pagesize`) |
| `get_contact` | `GET /v3/Contacts/{id}` |
| `list_bank_statements` | `GET /v3/BankStatements` (`filter`, `filtertype`, `sort`, `page`, `pagesize`) |
| `list_issued_payments` | `GET /v3/IssuedDocumentPayments` (`filter`, `filtertype`, `sort`, `page`, `pagesize`) |
| `get_current_agenda` | `GET /v3/Account/CurrentAgenda` |

Safe-test volá `GET /v3/Account/CurrentAgenda`.

Filtry jsou strukturované (`filters: [{field, operator, value}]`, spojené podle
`filter_type` `and`/`or`) a překládají se na dokumentovanou syntaxi
`(Prop~op~value~and~Prop~op~value)`. Sloupce filtru i řazení jsou omezené na
allow-list z dokumentace každého endpointu (např. vydané faktury: `Id`,
`DateOfIssue`, `DateOfMaturity`, `PartnerId`, `PaymentStatus`, `DocumentNumber`,
`TagIds`…; řazení `Id`, `DocumentNumber`, `DateOfIssue`). Operátory: `eq`, `!eq`,
`ct`, `!ct`, `lt`, `lte`, `gt`, `gte`. Hodnoty z bezpečné abecedy (čísla, seznamy
id, ISO data, názvy enumů) se posílají přímo; ostatní text se kóduje jako
`op:base64` podle doporučení dokumentace. Stránkování je 1-based, `page_size`
≤ 100. Parametry `include`/`select` se nepoužívají.

## Limity a PII

Odpověď identity serveru je omezena na 64 KiB, odpověď API na 2 MiB a bezpečný
výstup na 256 KiB; nad limitem vrací adaptér chybu, nikoli zkrácená data.
Vrací se pouze obsah dokumentované obálky `Data`; neprázdné `Message` nebo
nenulový `ErrorCode` znamenají chybu bez propagace textu providera. Neznámé
texty a názvy polí jsou pseudonymizované per uživatel/workspace/instalace/
client_id; jde o pseudonymizaci, ne anonymizaci. iDoklad omezuje API na
200 požadavků/min (429) a měsíční kvótu podle tarifu.

## Záměrně vynecháno

Žádné zápisy (`POST/PATCH/DELETE` dokladů, kontaktů, úhrad, odeslání e-mailů,
webhooky), žádné `Default`/`Copy`/`Recurrence` endpointy (připravují data pro
zápis), žádné PDF/obrázky reportů ani přílohy (binární obsah), žádné
`Logs`, `Statistics` ani číselníky. Endpoint `/Account/Agendas/Current`
v dokumentaci neexistuje — použit je dokumentovaný `/Account/CurrentAgenda`.

## Ověřeno podle

- https://api.idoklad.cz/Help/v3/en/ (apidoc data `api_data.json`/`api_project.json`:
  seznam GET endpointů, sloupce `filter`/`sort`, syntaxe filtrů, `page`/`pagesize`,
  obálka `Data`/`Message`/`StatusCode`/`ErrorCode`, limity, sekce Authorization a
  ClientCredentialsFlow s `POST https://identity.idoklad.cz/server/v2/connect/token`)
- https://developer.idoklad.cz/ (Developer portál, `application_id`)
- https://github.com/Solitea/IdokladSdk (oficiální SDK: `DokladConfiguration`
  s `IdentityServerTokenUrl = https://identity.idoklad.cz/server/v2/connect/token`,
  `ClientCredentialsTokenRequest` s poli `grant_type`, `application_id`,
  `client_id`, `client_secret`, `scope=idoklad_api`)

Mock testy nenahrazují přejímku skutečného účtu.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"client_id": "…", "client_secret": "…", "application_id": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_idoklad local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/idoklad.json \
  python -m connector_idoklad local call --privacy balanced list_issued_invoices
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_idoklad mcp`
nebo `docker run -i … openmcp-connector-idoklad mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py idoklad --credentials $HOME/.openmcp/idoklad.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
