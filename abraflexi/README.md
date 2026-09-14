# ABRA Flexi — read-only MCP konektor

Osmnáct čtecích nástrojů nad JSON REST API ABRA Flexi Cloud
(`https://<tenant>.flexibee.eu:5434`): firma, katalog povolených evidencí,
stránkované seznamy a detaily dokladů, sumace, metadata polí, vydané faktury,
přijaté objednávky, skladové pohyby a stavy, ceník. Žádné zápisy.

Rychlá vývojová kontrola z kořene repa: `make test-connector CONNECTOR=abraflexi`
(SDK + tento adaptér). Postup pro založení dalšího adaptéru je v
[průvodci](../docs/adding-connectors.md).

## Nástroje a bezpečnost

Zachováno všech 18 názvů read nástrojů:

- `get_company_info`, `list_companies`, `list_evidences`;
- `list_records`, `get_record`, `sum_records`, `get_evidence_properties`;
- `list_issued_invoices`, `get_issued_invoice`, `list_invoice_types`,
  `get_invoice_journal`;
- `list_received_orders`, `get_received_order`;
- `list_stock_movements`, `get_stock_movement`, `get_stock_status`;
- `list_products`, `get_product`.

Runtime nemá write tools. Historické `update_invoice_header`,
`update_invoice_item`, `create_stock_movement`, `update_stock_movement_items`
nejsou registrovány. Neexistuje raw URL, SQL ani volný Flexi filter argument.
Generické evidence mají pevný patnáctipoložkový allowlist v `schemas.py`;
jeho aktuální strojově čitelný katalog vrací `list_evidences`. Technické,
bezpečnostní, mzdové a vlastní dotazové evidence jsou odmítnuty.

Credentials `api_url`, `company`, `username`, `password`, `pii_key` pocházejí
pouze z body vázaného podpisem k tenantovi, instalaci, toolu a jednorázovému
požadavku. `secret_ref` musí přesně odpovídat
`abraflexi/<workspace_id>/<installation_id>` a mít platnou verzi. PII klíč
má nejméně 32 bytů. Runtime nečte provider credentials z env ani OpenBao.

Povolen je jen jednoúrovňový cloudový HTTPS origin
`https://<tenant>.flexibee.eu:5434` a identifikátor firmy `[a-z0-9_]{1,128}`. Každý
HTTP klient žije pouze během requestu; ověřuje TLS, nepřebírá proxy z env,
nesleduje redirecty a přijímá nejvýše 1 MiB odpovědi. Basic credentials
ani provider URL/filtry nejdou do výsledku/provenance. Výstup má nejvýše
256 KiB a povinnou konzervativní pseudonymizaci vázanou také na firmu;
názvy, popisy a neznámá pole se maskují, včetně firemních/katalogových dat.
Validované známé numerické hodnoty zachovávají přesný desetinný text.
Obsah je označen jako nedůvěryhodná externí data, nikoli instrukce.

## Stránkování a převod

Jedno volání vrací kompletní upstream stránku o 1–100 položkách; `offset`
je offset **upstream záznamů**, ne počet již nalezených shod. `next_offset`
udává navazující stránku, `truncated` možnost pokračování a `scanned`
skutečný počet přečtených řádků. Překročení limitu, neznámý response shape,
nesmyslný row count nebo chyba providera nejsou prázdný úspěšný výsledek.

Filtry názvu/skupiny produktu a skladu/směru pohybu se vyhodnocují lokálně
nad každou celou stránkou; serverové ad-hoc filtry jsou podle historických
zjištění nespolehlivé. I prázdná stránka shod může mít `next_offset`: klient
musí pokračovat tímto offsetem. Celkový počet shod se neodhaduje (`total=null`).
Žádné záznamy se nespotřebují za vrácenou stránkou. Datumové filtry, stav
úhrady a bezpečné generické AND filtry se skládají výhradně z typovaných
polí/operátorů/literálů; žádná interpolace nevalidovaných výrazů.

`test_connection` je malý autentizovaný GET ceníku s `limit=1`. Úspěch
potvrzuje jen přístup k této evidenci, ne všechna práva provider účtu.

## Přiznaná omezení a zbývající přejímka

- Port používá dokumentované `.json` endpointy a striktní `winstrom`,
  `companies` a `properties` obálky. Historická implementace preferovala XML
  po problémech konkrétních JSON volání; JSON jako formát je oficiálně
  podporován. Živá přejímka proti ABRA Flexi Cloud (14. 9. 2026, verze
  serveru 2026.5.3) prošla pro všech 18 nástrojů; tvary, které se lišily od
  dokumentace, jsou zachyceny v mock testech:
  - `properties.json` identifikuje evidenci v `tagName` (`evidenceName` je
    lidský název); výstup je projekce na dokumentované atributy polí
    (`name`, `type`, `title`, `mandatory`, `writable`, `sortable`,
    `in_summary`, `in_detail`, `max_length`, `digits`, `decimal`, `relation`,
    `values` = klíče výběrových hodnot), čitelná v každém režimu.
  - `$sum.json` vrací skupiny `winstrom.sum.<skupina>.values.<metrika>.value`
    (částky i v exponenciálním zápisu `2.709961007E7`); nástroj je normalizuje
    na `totals.<skupina>.<metrika>` s přesným desetinným textem a
    `by_currency[]` pro cizoměnové skupiny. Plochý dokumentovaný tvar
    `sumCelkem…` zůstává přijímán.
  - Položky dokladu přicházejí i bez `relations` pod `polozkyFaktury`,
    `polozkyObchDokladu` nebo `skladovePolozky`; `include_items=false`
    (faktura, objednávka, skladový pohyb) nebo `get_record` bez `polozky` je
    odstraní. Skladový pohyb se stovkou položek překračuje limity odpovědi,
    proto čti hlavičku bez položek a položky přes `skladovy-pohyb-polozka`.
  - Detail podle kódu (`ABC` nebo `code:ABC`) se čte filtrem `(kod='ABC')`,
    protože `/code:ABC.json` odpovídá přesměrováním; dvě shody jsou chyba
    providera, žádná shoda je `not_found`. `ext:` identifikátory jdou cestou.
  - `stav-skladu-k-datu` vyžaduje datum a sklad, proto je dostupný jen přes
    `get_stock_status`; `list_records` jej odmítne jako neplatný vstup.
- V režimu `balanced` jsou `kod` a `nazev` evidencí `adresar` a `kontakt`
  osobní údaje (jméno partnera) a pseudonymizují se; v ostatních evidencích
  zůstávají obchodním údajem.
- `list_companies` nyní čte **jen firmu vázanou na instalaci**, nikoli všechny
  databáze sdíleného provider účtu. Jde o záměrně užší kontrakt.
- Širší historický registr přibližně 180 evidencí není přenesen celý. Podporu
  určuje pouze zdejší allowlist; nikoli historie nebo provider discovery.
- Generické operátory `like_similar`, `begins_similar`, `is_empty`,
  `is_not_empty`, více než jedna úroveň relačního pole a přílohy `prilohy`
  jsou odložené. Podporované relations jsou `polozky` a `vazby`.
- Numerická ID a bezpečné kódy fungují přímo; redirect-based external-ID
  resolving se nepřebírá. `ext:` odpověď s redirectem bezpečně selže.
  Kódy s mezerami/diakritikou jsou zatím mimo kontrakt.
- Sumace je povolena pouze nad doklady `faktura-vydana`, `objednavka-prijata`,
  `skladovy-pohyb`; nedokladové evidence odmítá vstupní schema. Neznámé
  agregační struktury selžou jako neplatná data providera. Neprohlašujeme
  úplnou účetní či historickou paritu.
- Pseudonymizace neznámých metadata/description polí může omezit užitečnost
  samodokumentace. Rozšíření čitelných polí vyžaduje vědomý PII review.
-

Testy/buildy spouštěj z kořene repa přes Docker (`make test-python`,
`make build-python`). Provider testy používají pouze `httpx.MockTransport`
a syntetická data, bez skutečných účtů.

Primární dokumentace: [JSON výstup a URL API](https://podpora.flexibee.eu/en/articles/3638743-how-to-get-started-with-flexi-api-3-6),
[referenční JSON obálka a řetězcové success](https://www.flexibee.eu/api/dokumentace/ref/),
[identifikátor firmy a company API](https://podpora.flexibee.eu/en/articles/4425606-company-identifier-company-id-and-a-list-of-companies-created-via-api),
[sumace dokladů](https://podpora.flexibee.eu/cs/articles/4722199-sumace).

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"api_url": "…", "username": "…", "password": "…", "company": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_abraflexi local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/abraflexi.json \
  python -m connector_abraflexi local call --privacy balanced get_company_info
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_abraflexi mcp`
nebo `docker run -i … openmcp-connector-abraflexi mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py abraflexi --credentials $HOME/.openmcp/abraflexi.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
