# ARES — read-only MCP konektor

Dva čtecí nástroje nad veřejným REST API Administrativního registru
ekonomických subjektů (`https://ares.gov.cz/ekonomicke-subjekty-v-be/rest`):
ověření firmy podle IČO a vyhledání podle obchodního jména (volitelně adresy).
Nepotřebuje žádné credentials ani registraci. IČO se před voláním ověřuje
včetně kontrolního součtu; výstup obsahuje jen veřejná registrová data
(název, právní forma, sídlo, DIČ, CZ-NACE, registrace) s provenance URL.

## Nástroje

| nástroj | popis |
|---|---|
| `ares_subjekt_lookup` | Detail subjektu podle osmimístného IČO. |
| `ares_subjekt_vyhledat` | Hledání subjektů podle obchodního jména a volitelně adresy (stránkování `start`/`pocet`, nejvýše 50). |

## Lokální použití

```sh
python -m connector_ares local tools
printf '%s' '{"ico":"27074358"}' | python -m connector_ares local call ares_subjekt_lookup
printf '%s' '{"obchodni_jmeno":"ABRA Software","pocet":5}' | python -m connector_ares local call ares_subjekt_vyhledat
```

Přes Docker (`docker build -f ares/Dockerfile -t openmcp-connector-ares:local .`)
je to `docker run --rm -i openmcp-connector-ares:local local call …`. Výsledek je
jeden JSON objekt na stdout; bezpečně formulované chyby jdou jako JSON na stderr
s nenulovým exit kódem.

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_ares mcp`
nebo `docker run -i … openmcp-connector-ares:local mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py ares --claude-code`. Podrobnosti:
[docs/local-cli.md](../docs/local-cli.md), [docs/mcp-stdio.md](../docs/mcp-stdio.md).
