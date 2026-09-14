# OpenMCP konektory

34 read-only MCP konektorů pro české a slovenské služby — účetnictví, e-shopy,
platby, banky, CRM, marketing, logistika, registry — se společným Python SDK.
Každý konektor umí běžet jako **stdio MCP server** pro Claude Desktop / Claude
Code, jako **lokální CLI** bez MCP klienta, nebo jako hostovaný runtime v
[OpenMCP](https://openmcp.cz).

Společné vlastnosti všech konektorů:

- **Jen čtení.** Žádný konektor nemá write nástroj; transportní vrstva odmítne
  cokoli mimo dokumentovaný allow-list metod a cest ještě před odesláním.
- **Pevný egress.** Každý adaptér volá jediný ověřený HTTPS origin providera
  (případně tenant host z validovaných credentials); redirecty, proxy z
  environmentu a URL z argumentů nástroje se nepřijímají.
- **Ochrana osobních údajů.** Výstup prochází pseudonymizací ve třech režimech:
  `strict` (výchozí — jen allowlistovaná pole a metriky), `balanced`
  (obchodní data čitelná, osobní údaje a volný text maskované) a `plain`.
  Hodnoty shodné s credentials jsou maskované vždy.
- **Bezpečné chyby.** Odpověď providera ani výjimka se nikdy nevrací; klient
  dostane strukturovanou chybu (`invalid_input`, `credential_invalid`,
  `not_found`, `upstream_error`, …).
- **Limity.** Argumenty do 64 KiB, odpověď providera do 1–2 MiB, výstup do
  256 KiB; nad limit je chyba, ne oříznutá data.

## Konektory

| Konektor | Oblast | Nástroje | Credentials |
|---|---|---:|---|
| [ARES](ares/) | registry | 2 | — (veřejné API) |
| [Dotykačka](dotykacka/) | pos | 3 | `refresh_token`, `cloud_id` |
| [ABRA Flexi](abraflexi/) | erp | 18 | `api_url`, `username`, `password`, `company` |
| [RAYNET](raynet/) | crm | 16 | `instance_name`, `username`, `api_key` |
| [Upgates](upgates/) | ecommerce | 23 | `api_url`, `api_login`, `api_key` |
| [SuperFaktúra](superfaktura/) | accounting | 6 | `region`, `email`, `api_key` |
| [Fakturoid](fakturoid/) | accounting | 6 | `account_slug`, `access_token` |
| [Freelo](freelo/) | project_management | 4 | `email`, `api_key` |
| [FinStat](finstat/) | company_register | 7 | `api_key`, `private_key` |
| [Packeta](packeta/) | logistics | 7 | `api_password`, `api_key` |
| [Websupport](websupport/) | hosting | 7 | `api_key`, `api_secret` |
| [Marketing Miner](marketingminer/) | marketing | 4 | `api_token` |
| [Mapy.com](mapy/) | maps | 5 | `api_key` |
| [Shoptet](shoptet/) | ecommerce | 8 | `access_token` |
| [Daktela](daktela/) | customer_support | 7 | `instance_url`, `access_token` |
| [SupportBox](supportbox/) | customer_support | 6 | `api_token` |
| [Mews](mews/) | hospitality | 6 | `client_token`, `access_token`, `environment` |
| [Rossum](rossum/) | document_processing | 7 | `base_url`, `api_token` |
| [Keboola](keboola/) | data_platform | 8 | `stack`, `storage_token` |
| [iDoklad](idoklad/) | accounting | 8 | `client_id`, `client_secret`, `application_id` |
| [KROS Fakturácia](kros/) | accounting | 8 | `api_token` |
| [FAPI](fapi/) | accounting | 8 | `username`, `api_key` |
| [Vyfakturuj.cz](vyfakturuj/) | accounting | 8 | `email`, `api_key` |
| [POHODA mServer](pohoda/) | erp | 8 | `mserver_url`, `username`, `password`, `ico` |
| [GoPay](gopay/) | payments | 4 | `goid`, `client_id`, `client_secret`, `environment` |
| [Comgate](comgate/) | payments | 4 | `merchant`, `secret` |
| [Fio banka](fio/) | banking | 4 | `token` |
| [Heureka Marketplace](heureka/) | ecommerce | 3 | `api_key`, `country` |
| [SmartEmailing](smartemailing/) | email_marketing | 8 | `username`, `api_key` |
| [Ecomail](ecomail/) | email_marketing | 8 | `api_key` |
| [Collabim](collabim/) | seo | 8 | `api_key` |
| [Sklik](sklik/) | advertising | 6 | `api_token`, `user_id` |
| [Reservio](reservio/) | booking | 8 | `access_token`, `business_id` |
| [Luigi's Box](luigisbox/) | search | 6 | `tracker_id`, `public_key`, `private_key` |

Podrobnosti, seznam nástrojů, získání credentials a známá omezení má každý
konektor ve svém `README.md`; strojově čitelný manifest je `connector.yaml`.

## Rychlý start

Python ≥ 3.12 (nebo Docker). Credentials patří do JSON souboru čitelného jen
vlastníkem — klíče podle tabulky výše, `pii_key` je volitelný řetězec ≥ 32
znaků pro stabilní pseudonymizační tokeny mezi voláními:

```sh
pip install ./sdk ./freelo
umask 077
printf '%s' '{"email":"jan@firma.cz","api_key":"…"}' > ~/.openmcp/freelo.json

python -m connector_freelo local tools
printf '%s' '{"page":1}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/freelo.json \
  python -m connector_freelo local call --privacy balanced list_tasks
```

Veřejný ARES nepotřebuje credentials:

```sh
printf '%s' '{"ico":"27074358"}' | python -m connector_ares local call ares_subjekt_lookup
```

### Jako MCP server pro Claude Desktop / Claude Code

```sh
python3 scripts/mcp_config.py freelo --credentials ~/.openmcp/freelo.json --python
python3 scripts/mcp_config.py ares --claude-code
```

První příkaz vypíše blok pro `claude_desktop_config.json`, druhý příkaz
`claude mcp add-json …`. Server běží přes stdio (`python -m connector_<slug> mcp`),
podporuje MCP `2024-11-05` až `2026-07-28` a každý nástroj nese
`readOnlyHint: true`. Podrobnosti: [docs/mcp-stdio.md](docs/mcp-stdio.md).

### Docker

Build context je kořen repozitáře (image instaluje lokální `sdk/`):

```sh
docker build -f freelo/Dockerfile -t openmcp-connector-freelo:local .
printf '%s' '{"page":1}' | docker run --rm -i --user "$(id -u):$(id -g)" \
  -e OPENMCP_LOCAL_CREDENTIALS_FILE=/run/creds.json \
  -v "$HOME/.openmcp/freelo.json:/run/creds.json:ro" \
  openmcp-connector-freelo:local local call list_tasks
```

Image běží jako UID 10001, bez zápisu do pracovního adresáře. Spuštění bez
argumentu je hostovaný runtime (interní HTTP server s podepsanými požadavky),
který bez konfigurace platformy odmítne každé volání.

## Režimy ochrany dat

| Režim | Chování |
|---|---|
| `strict` | Výchozí. Čitelné jsou jen allowlistované klíče, metriky, data a příznaky; ostatní klíče i hodnoty jsou nahrazeny HMAC tokeny (`<KEY_…>`, `<FIELD_…>`). Nejbezpečnější, ale u bohatých evidencí zbývá málo čitelného. |
| `balanced` | Obchodní data (částky, data, kódy dokladů, stavy, ID) zůstávají čitelná. Maskují se známá PII pole (anglické i české/slovenské názvy — jméno, adresa, e-mail, telefon, IBAN, datum narození…), celé větve `contact`/`customer`/`address`/`kontakt`/`odberatel`…, volné texty (`note`, `popis`, `poznam`…) a e-maily/telefony/IBAN/URL uvnitř ostatních textů. Adaptér může označit generické pole jako osobní podle evidence (např. `nazev` partnera v adresáři). |
| `plain` | Bez pseudonymizace provider dat. Hodnoty shodné s credentials jsou maskované i zde. |

Tokeny jsou HMAC vázané na `pii_key`, uživatele, instalaci a provider scope:
stejná hodnota dává v jednom kontextu stabilní token, napříč kontexty
nekorelovatelný. Jde o pseudonymizaci, ne anonymizaci — výstup je vždy označen
`content_origin: untrusted_external_data_not_instructions`. Implementace:
[sdk/README.md](sdk/README.md).

## Struktura repozitáře

```
sdk/            společný runtime: podepsaný invocation kontrakt, upstream klient,
                pseudonymizace, lokální CLI a stdio MCP server
<slug>/         jeden konektor: src/connector_<slug>/, tests/, connector.yaml,
                Dockerfile, pyproject.toml, README.md
connectors.list registr konektorů (jediný zdroj pro testy a build)
scripts/        new_connector.py (scaffold), connector_inventory.py (registr),
                manifests.py (generování connector.yaml), mcp_config.py
docs/           local-cli.md, mcp-stdio.md, adding-connectors.md
```

## Vývoj

Testy běží v Dockeru bez sítě (ruff, mypy strict, pytest s `-W error`,
kontrola driftu manifestů, non-root smoke obrazů):

```sh
make test                                 # SDK + všech 34 konektorů + registr + manifesty
make test-connector CONNECTOR=abraflexi   # SDK + jeden adaptér
make build                                # obrazy všech konektorů + smoke
```

Provider testy používají výhradně `httpx.MockTransport` a syntetická data.
Co bylo ověřeno proti živému účtu, uvádí README konektoru. Nový konektor:
[docs/adding-connectors.md](docs/adding-connectors.md).

## Hostovaný provoz

Stejné balíčky běží v OpenMCP jako interní HTTP runtime: přijímají jen
požadavky podepsané HMAC-SHA-256 tokenem s krátkým TTL, jednorázovým `jti` a
vazbou na přesné JSON bytes těla, tenant, instalaci, nástroj a verzi
manifestu; replay ochrana je distribuovaná přes Valkey. Credentials providera
přicházejí výhradně v podepsaném těle požadavku od core — runtime nemá
přístup k trezoru ani k credentials jiných instalací. Kontrakt a konfigurace:
[sdk/README.md](sdk/README.md).

## Licence

[MIT](LICENSE). Konektory volají API třetích stran; podmínky jejich použití
určuje příslušný provider.
