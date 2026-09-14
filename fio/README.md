# Fio banka — read-only MCP konektor

Adaptér publikuje čtyři čtecí nástroje nad Fio API Bankovnictví
(`https://fioapi.fio.cz/v1/rest`). Výchozí interní port je 8127. Nikdy
nenastavuje zarážky (`set-last-id`, `set-last-date`) ani neimportuje platební
příkazy.

## Nástroje

| Nástroj | Provider endpoint (vždy `GET`) |
|---|---|
| `list_transactions` | `/periods/{token}/{od}/{do}/transactions.json` — období nejvýše 90 dní |
| `last_transactions` | `/last/{token}/transactions.json` — pohyby od posledního stažení |
| `get_statement` | `/by-id/{token}/{rok}/{číslo}/transactions.json` — oficiální výpis |
| `last_statement_number` | `/lastStatement/{token}/statement` — rok a číslo posledního výpisu |

Safe-test volá `lastStatement` (neposouvá zarážku). Odpověď `accountStatement`
se vrací jako `info` (hlavička účtu), `items` (pohyby) a `count`. Sloupce
pohybu (`column22` = ID pohybu, `column0` = Datum, `column1` = Objem, …) se
překládají podle dokumentovaného `name` na stabilní klíče (`id`, `date`,
`amount`, `currency`, `variableSymbol`, `message`, `type`, `instructionId`, …);
neznámé sloupce si ponechají `column{id}`.

**`last_transactions` je čtecí, ale má vedlejší účinek na straně banky:** podle
specifikace Fio po každé neprázdné odpovědi automaticky posune zarážku
posledního staženého pohybu. Další volání vrátí jen novější pohyby; adaptér to
hlásí ve `warnings`. Pro opakovatelné čtení použijte `list_transactions`.
Odpověď `lastStatement` je prostý text `rok,číslo`; adaptér přijme jen tento
tvar a rok/číslo zopakuje ve `warnings` (klíč `year` by jinak konzervativní
pseudonymizace znečitelnila).

## Credentials a egress

`token` (64znakový řetězec vygenerovaný v internetovém bankovnictví; jiný tvar
selže před požadavkem) a povinný `pii_key`, jen v podepsaném body svázaném
s instalací. Token je součástí URL cesty: nikdy se neloguje, provenance
obsahuje placeholder `{token}` a testy ověřují, že se neobjeví ve výstupu ani
v chybách. Egress: `fioapi.fio.cz:443`, prefix `/v1/rest`, pouze `GET`.

## Limity a ochrana dat

Fio vyžaduje nejméně 30 s mezi dotazy se stejným tokenem; porušení vrací
`409 Conflict`, které adaptér mapuje na `rate_limited` (retryable) a nikdy
automaticky neopakuje (jediný pokus na volání). `500` znamená podle
specifikace neexistující nebo neaktivní token (`credential_invalid`), `404`
chybné parametry URL (`not_found`). Období `list_transactions` je omezeno na
90 dní na dotaz (adaptérový limit; data starší 90 dní navíc vyžadují dočasné
odemknutí v internetovém bankovnictví). Upstream odpověď max. 4 MiB, výsledek
max. 256 KiB. Názvy protiúčtů, zprávy, komentáře a neznámá pole jsou
pseudonymizovány přes `private_envelope`.

## Co adaptér nenabízí

- `merchant/{token}/{od}/{do}` (karetní transakce obchodníka): specifikace ho
  uvádí pouze ve formátu XML, JSON není dokumentován.
- Formáty jiné než JSON (XML, CSV, GPC, OFX, PDF, MT940, camt.053).
- Nastavení zarážek a import příkazů (zápisové operace).

## Ověřeno podle

- https://www.fio.cz/bankovni-sluzby/api-bankovnictvi
- https://www.fio.cz/docs/cz/API_Bankovnictvi.pdf (specifikace API
  Bankovnictví, verze 16. 10. 2025: struktura URL `periods`, `by-id`, `last`,
  `lastStatement`, `set-last-id`, `set-last-date`, `merchant`; token 64 znaků;
  minimální interval 30 s; stavové kódy 404/409/500; JSON struktura
  `accountStatement` → `info`, `transactionList.transaction`, sloupce
  `column{id}` s `value`/`name`/`id`)

Mock testy nenahrazují přejímku se skutečným účtem.

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"token": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_fio local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/fio.json \
  python -m connector_fio local call --privacy balanced list_transactions
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_fio mcp`
nebo `docker run -i … openmcp-connector-fio mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py fio --credentials $HOME/.openmcp/fio.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
