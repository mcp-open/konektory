# Sklik — read-only MCP konektor

Adaptér publikuje šest čtecích nástrojů nad Sklik API Drak v JSON variantě
(`https://api.sklik.cz/drak/json/v5`). Výchozí interní port je 8133.

## Nástroje

| Nástroj | Metody API Drak (vždy `POST /drak/json/v5/<metoda>`) |
| --- | --- |
| `get_client` | `client.get` (atributy účtu a `foreignAccounts` – spravované účty) |
| `list_campaigns` | `campaigns.list` (`restrictionFilter.ids`, `isDeleted`; `displayOptions.offset/limit`) |
| `list_groups` | `groups.list` (`restrictionFilter.ids`, `campaign.ids`, `isDeleted`; `offset/limit`) |
| `list_keywords` | `keywords.list` (`ids`, `campaign.ids`, `group.ids`, `isDeleted`; `offset/limit`) |
| `list_ads` | `ads.list` (`ids`, `campaign.ids`, `group.ids`, `isDeleted`; `offset/limit`) |
| `campaign_stats` | `campaigns.createReport` (`dateFrom`, `dateTo`, `ids`, `isDeleted`; `statGranularity`) + `campaigns.readReport` (`reportId`, `offset/limit`, pevné `displayColumns`) |

Každé volání nástroje je jedna krátká relace: `client.loginByToken` (token v
těle požadavku, přesně podle oficiální Postman kolekce) → čtecí metoda →
`client.logout` (vždy, i při chybě; selhání odhlášení se ignoruje, relace
sama vyprší). Safe-test volá `client.get`. API Drak přijímá výhradně
`HTTP POST` s JSON tělem; adaptér proto povoluje POST, ale jen na devět
přesně vyjmenovaných metod (`ALLOWED_METHODS` v `service.py`: dvě session
metody a sedm čtecích). `createReport` jen vytvoří dočasný serverový pohled
na statistiky (dokumentovaný čtecí postup createReport → readReport) a nemění
žádná data účtu. Žádné `create`/`update`/`remove`/`restore`/`set`/`check`
metody nejsou dostupné; testy ověřují, že se nikdy nepošlou, ani jiná URL
než `api.sklik.cz`. Statistiky sestav/klíčových slov/inzerátů
(`groups.createReport` atd.), `client.stats`, `client.getCredit` a XML-RPC
endpoint `/drak/RPC2` jsou záměrně vynechané.

Stránkování je providerovo `displayOptions: {offset (0–100000), limit
(1–500)}`. Výchozí filtr `isDeleted: false`; `include_deleted: true` filtr
vynechá (provider pak vrací smazané i nesmazané). Období statistik je
povinné a ohraničené na jeden rok; provider navíc omezuje objem reportu
(`api.limits.statsDataLimit`) a větší report odmítne sám. Odpověď seznamu
obsahuje `data` a `count`, statistiky navíc `totalCount` z `createReport`.

## Credentials a egress

Credentials přicházejí pouze v podepsaném body svázaném s instalací:
`api_token` (API token z nastavení účtu Sklik), volitelný `user_id`
(číselné ID spravovaného účtu; posílá se jako `userId` ve struktuře `user`,
bez něj se čte vlastní účet) a nejméně 32bytový `pii_key`. Token se posílá
výhradně jako tělo `client.loginByToken`; session string jen v JSON těle
dalších metod, nikdy v URL ani hlavičce, a nikdy se nevrací ve výstupu.
Egress: POST na `api.sklik.cz:443`, prefix `/drak/json/v5`.

## Limity a ochrana dat

Provider vrací stav v těle (`status`): 200/206 úspěch, 401/403 →
`credential_invalid`, 404 → `not_found`, 429 → `rate_limited` (retryable,
čtecí metody se opakují nejvýše dvakrát), 5xx → `upstream_unavailable`,
400/406/413 a ostatní → `upstream_error`; HTTP chyby se mapují stejně.
`statusMessage` ani `diagnostics` providera se nikdy nepropisují do chyby.
Upstream odpověď max. 4 MiB, výsledek max. 256 KiB, jinak chyba (nikdy tiché
zkrácení). Neznámá pole a texty (názvy kampaní, klíčová slova, texty
inzerátů, URL, uživatelská jména) jsou pseudonymizované přes
`private_envelope`; čitelné zůstávají jen vybraná číselná/boolean/datová
pole podle seznamu SDK (`id`, `count`, `totalCount`, `status`, `type`,
`name` jako klíč – hodnota je pseudonym; metriky jako `clicks` zůstávají
pod pseudonymizovaným klíčem). Konzervativní politika je záměrná.

Ověřeno podle oficiální dokumentace Sklik API Drak
(https://api.sklik.cz/drak/ – Introduction/Versions/JSON API,
https://api.sklik.cz/drak/usage.html, client.loginByToken.html,
client.logout.html, client.get.html, campaigns.list.html, groups.list.html,
keywords.list.html, ads.list.html, campaigns.createReport.html,
campaigns.readReport.html) a oficiálních příkladů
https://github.com/seznam/api-examples (Postman kolekce API Drak, PHP/Python
JSON klienti). Mock testy nenahrazují přejímku skutečného účtu.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"api_token": "…", "user_id": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_sklik local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/sklik.json \
  python -m connector_sklik local call --privacy balanced get_client
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_sklik mcp`
nebo `docker run -i … openmcp-connector-sklik mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py sklik --credentials $HOME/.openmcp/sklik.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
