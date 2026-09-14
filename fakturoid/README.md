# Fakturoid — read-only MCP konektor

Adaptér publikuje šest čtecích nástrojů pro vydané doklady, kontakty a náklady
nad `https://app.fakturoid.cz/api/v3`. Přijímá `account_slug`, krátkodobý OAuth
`access_token` a povinný `pii_key` pouze v přesně podepsaném invocation body.
OAuth Authorization Code flow, refresh a revokaci vlastní platforma; runtime
nikdy nepřijímá client secret ani sám neobnovuje token.

Všechny cesty jsou odvozené jen z validovaného account slugu a číselného ID.
Runtime posílá providerem vyžadovaný identifikační User-Agent, nesleduje
redirecty, nepoužívá proxy z prostředí a omezuje vstupní i pseudonymizovaný
výstup. Nevytváří doklady, platby, zprávy, webhooky ani soubory.

Mock testy neprokazují živý OAuth účet. Registrace Fakturoid integrace,
platformní OAuth callback, OpenBao lifecycle, mTLS/egress a reálná provider
přejímka musí proběhnout před aktivací.

## Nástroje

| nástroj | popis |
|---|---|
| `list_invoices` | Stránka vydaných dokladů a stavů úhrad. |
| `get_invoice` | Detail vydaného dokladu podle ID. |
| `list_subjects` | Stránka kontaktů a firem v adresáři. |
| `get_subject` | Detail kontaktu nebo firmy podle ID. |
| `list_expenses` | Stránka nákladů a přijatých dokladů. |
| `get_expense` | Detail nákladu podle ID. |

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"account_slug": "…", "access_token": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_fakturoid local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/fakturoid.json \
  python -m connector_fakturoid local call --privacy balanced list_invoices
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_fakturoid mcp`
nebo `docker run -i … openmcp-connector-fakturoid mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py fakturoid --credentials $HOME/.openmcp/fakturoid.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
