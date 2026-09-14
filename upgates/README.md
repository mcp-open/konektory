# Upgates — read-only MCP konektor

Dvacet tři čtecích nástrojů nad Upgates API v2 (`https://<tenant>.admin.upgates.com/api/v2`):
objednávky, produkty, zákazníci, kategorie, kupóny, doprava, platby a nastavení
e-shopu. Žádné zápisy.

Credentials přicházejí pouze ve verzi svázané s podepsaným body:
`api_url`, `api_login`, `api_key` a nejméně 32bytový `pii_key`.
`secret_ref` musí přesně odpovídat `upgates/{workspace_id}/{installation_id}`.
Podporovaný origin je pouze `https://{tenant}.admin.upgates.com/api/v2`
(jeden DNS label, port 443). Vlastní domény, HTTP, redirecty a proxy z
environmentu jsou odmítnuté. Poskytovateli se posílá pouze Basic auth,
nikoli platformní token nebo kontext uživatele.

## Nástroje

| nástroj | popis |
|---|---|
| `list_orders` | Seznam objednávek s filtrováním a stránkováním (s omezením velikosti odpovědi).

Data zákazníka jsou před opuštěním konektoru pseudonymizována. |
| `get_order_history` | Historie konkrétní objednávky. Data zákazníka jsou pseudonymizována. |
| `list_order_statuses` | Seznam všech stavů objednávek. |
| `list_invoices` | Seznam faktur s filtrováním a stránkováním. Data zákazníka jsou pseudonymizována. |
| `list_products` | Seznam produktů s filtrováním a stránkováním (s omezením velikosti odpovědi). |
| `list_products_simple` | Seznam produktů ve zjednodušeném formátu (s omezením velikosti odpovědi). |
| `list_customers` | Seznam zákazníků s filtrováním a stránkováním. Osobní data jsou pseudonymizována. |
| `list_categories` | Seznam kategorií s filtrováním a stránkováním (s omezením velikosti odpovědi). |
| `list_labels` | Seznam štítků produktů (s omezením velikosti odpovědi). |
| `list_availabilities` | Seznam dostupností produktů (s omezením velikosti odpovědi). |
| `list_manufacturers` | Seznam výrobců (s omezením velikosti odpovědi). |
| `list_parameters` | Seznam parametrů produktů. |
| `list_carts` | Seznam košíků s omezením velikosti odpovědi a pseudonymizací zákazníků.

Bez filtru data nebo id se výchozí použijí košíky za posledních 7 dní. |
| `list_vouchers` | Seznam slevových kupónů (s omezením velikosti odpovědi). |
| `list_shipments` | Seznam způsobů dopravy (vícejazyčné popisy jsou zkráceny). |
| `list_payments` | Seznam platebních metod (vícejazyčné popisy jsou zkráceny). |
| `list_webhooks` | Seznam nakonfigurovaných webhooků. |
| `list_webhook_events` | Seznam dostupných událostí webhooku. |
| `get_languages` | Získej konfiguraci jazyků e-shopu. |
| `get_shop_config` | Získej konfiguraci a nastavení e-shopu. |
| `get_shop_owner` | Získej fakturační údaje provozovatele e-shopu. Firemní data jsou pseudonymizována. |
| `get_api_status` | Získej stav API a seznam povolených endpointů pro aktuálního uživatele. |
| `list_pricelists` | Seznam ceníků. |

## Rozsah

Objednávky a historie, stavy objednávek, faktury, produkty a zjednodušený
katalog, zákazníci, kategorie, štítky, dostupnosti, výrobci, parametry,
košíky, vouchery, doprava a platby, čtení webhooků/událostí, jazyky,
konfigurace/vlastník obchodu, stav API a ceníky. Přesná schémata obsahuje
`connector.yaml`; generátor `scripts/manifests.py` kontroluje jejich shodu
s runtime. Žádné vytváření webhooků ani jiné zápisy.

Filtry mají pevná jména a meze; datum je platné kalendářní `YYYY-MM-DD`,
stránka 1–10000. Stránky se nepřekračují automaticky. Výstup zachovává všechny
záznamy i vnější stránkování; nad limitem 1 MiB upstream / 256 KiB bezpečného
výsledku vrátí chybu, nikoli tiše zkrácený seznam.

PII ochrana je povinná a konzervativní: neznámé texty a názvy polí jsou
pseudonymizované; jen vybraná číselná/datová pole zůstávají čitelná.
Tokeny jsou oddělené podle uživatele, workspace, instalace a provider originu.
To omezuje čitelnost některých obchodních názvů, ale nepovoluje režim raw dat.
Pseudonymizace není anonymizace ani náhrada autorizace.

## Ověření a aktivace

WSL Docker mock testy pokrývají všech 23 mapování, validaci, izolaci credentials,
redirecty, neplatné/velké odpovědi a úplnost stránky. Přímá provider přejímka
potřebuje vyhrazený testovací účet a není nahrazena těmito testy.

Platformní katalog, credential provisioning, vlastní replay ACL a mTLS/egress
gateway se přidávají až v samostatné integrační dodávce.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"api_url": "…", "api_login": "…", "api_key": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_upgates local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/upgates.json \
  python -m connector_upgates local call --privacy balanced list_orders
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_upgates mcp`
nebo `docker run -i … openmcp-connector-upgates mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py upgates --credentials $HOME/.openmcp/upgates.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
