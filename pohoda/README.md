# POHODA mServer — read-only MCP konektor

Adaptér publikuje osm čtecích nástrojů nad XML rozhraním programu POHODA
zpřístupněným přes POHODA mServer (`POST /xml`). Výchozí interní port je 8124.
Odesílá výhradně exportní požadavky `list*Request`; žádný import, editace,
mazání, tisk ani jiný `*Request` mimo allow-list nikdy neopustí adaptér.

## Nástroje

| Nástroj | XML požadavek v `dat:dataPackItem` |
|---|---|
| `list_invoices` | `lst:listInvoiceRequest` (`invoiceType`, filtr `dateFrom`, `dateTill`, `selectedCompanys/company`, `selectedIco/ico`, `lastChanges`) |
| `get_invoice` | `lst:listInvoiceRequest` s filtrem `id`, `limit/count = 1` |
| `list_orders` | `lst:listOrderRequest` (`orderType`, stejné filtry jako faktury) |
| `get_order` | `lst:listOrderRequest` s filtrem `id` |
| `list_partners` | `lAdb:listAddressBookRequest` (filtr `company`, `name`, `city`, `ico`, `lastChanges`) |
| `get_partner` | `lAdb:listAddressBookRequest` s filtrem `id` |
| `list_stock` | `lStk:listStockRequest` (filtr `code`, `EAN`, `name`, `lastChanges`) |
| `get_stock_item` | `lStk:listStockRequest` s filtrem `id` |

Safe-test odešle `listAddressBookRequest` s `limit/count = 1`. Každý požadavek je
jeden `dat:dataPack` (`version="2.0"`, `application="OpenMCP"`, atribut `ico`
z credentials) s jedním `dat:dataPackItem`; stránkování používá dokumentovaný
element `limit` (`idFrom`, `count`). Seznamy vrací `items`, `count`,
`truncated`; detail vrací `data` nebo `not_found`. XML odpověď `rsp:responsePack`
se převádí na JSON (atributy jako `@state`, opakované elementy jako pole),
stav `error` na úrovni `responsePack`, `responsePackItem` i seznamu se mapuje na
bezpečnou chybu bez textu providera. DOCTYPE/ENTITY v odpovědi je odmítnut,
velikost XML je omezena na 4 MiB, hloubka na 32 úrovní.

## Credentials

`mserver_url`, `username`, `password` (přihlášení do POHODY; posílá se jako
`STW-Authorization: Basic base64(user:pass)`), `ico` (atribut `ico` dataPacku,
musí souhlasit s IČ účetní jednotky) a povinný `pii_key`. Přicházejí jen
v podepsaném body svázaném s instalací; nikdy se nelogují a provenance obsahuje
placeholder `https://{mserver_url}/xml`, ne adresu zákazníka.

## Egress — jediná výjimka z pevného egressu

mServer hostuje zákazník, proto origin pochází z credential `mserver_url`.
Přijímá se výhradně tvar `https://hostname[:port]` nebo `https://IPv4[:port]`:
bez cesty, query, fragmentu a userinfo, bez IPv6, řídicích znaků, mezer a
zpětných lomítek, hostname z platných DNS labelů (max. 253 znaků). Cokoli
jiného selže `credential_invalid` před jakýmkoli požadavkem (testy to
kontrolují). mServer sám mluví HTTP; **zákazník ho musí vystavit za HTTPS
reverse proxy** (nebo nativní HTTPS mServeru) s platným certifikátem — adaptér
nikdy nepoužije `http://`. Manifest deklaruje `egress.host: customer_mserver`,
`path_prefix: /xml`, `methods: [POST]`; port je dán credential (výchozí 443).

## Limity a ochrana dat

`count` 1–500 (výchozí 50; POHODA povoluje až 10 000, adaptér drží menší
stránky kvůli převodu a pseudonymizaci v paměti), období `date_from`–`date_till`
nejvýše 366 dní, `last_changes` ve tvaru `YYYY-MM-DDThh:mm:ss`. Tělo
požadavku je v kódování Windows-1250 (znaky mimo kódovou stránku se posílají
jako znakové reference). Výsledek max. 256 KiB. Firmy, jména, adresy a všechna
neznámá pole jsou pseudonymizována přes `private_envelope`. `queryFilter`
(volný SQL filtr) a `userFilterName` adaptér záměrně nenabízí; filtr podle
čísla dokladu není v `filterDocsType` dostupný (`selectedNumbers` odkazuje na
číselnou řadu). `GET /status` mServeru není použit, protože egress povoluje
jen `POST /xml`.

## Ověřeno podle

- https://www.stormware.cz/pohoda/xml/mserver/provyvojare/ (`POST /xml`,
  `STW-Authorization: Basic …`, `Content-Type: text/xml`, volitelné
  `STW-Application`/`STW-Instance`, kódování Windows-1250, stavové kódy
  400/401/403/404/405, příklad `listStockRequest`)
- https://www.stormware.cz/pohoda/xml/ (přehled XML komunikace)
- XSD schémata verze 2: https://www.stormware.cz/schema/version_2/data.xsd,
  list.xsd, list_addBook.xsd, list_stock.xsd, filter.xsd, response.xsd,
  documentresponse.xsd, invoice.xsd, order.xsd, addressbook.xsd, stock.xsd,
  type.xsd (`dataPack`/`dataPackItem`, `listInvoiceRequest` s `invoiceType`
  a `invoiceVersion`, `listOrderRequest` s `orderType`, `listAddressBookRequest`
  s `addressBookVersion`, `listStockRequest` se `stockVersion`, `limit`,
  `filterDocsType`, `filterAdbsType`, `filterStocksType`, `responsePack` a
  `state`)

Mock testy nenahrazují přejímku se skutečnou instalací POHODA.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"mserver_url": "…", "username": "…", "password": "…", "ico": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_pohoda local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/pohoda.json \
  python -m connector_pohoda local call --privacy balanced list_invoices
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_pohoda mcp`
nebo `docker run -i … openmcp-connector-pohoda mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py pohoda --credentials $HOME/.openmcp/pohoda.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
