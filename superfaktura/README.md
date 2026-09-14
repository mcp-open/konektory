# SuperFaktúra — read-only MCP konektor

Adaptér publikuje šest čtecích nástrojů pro faktury, klienty a náklady.
Podporuje pevné slovenské a české produkční i sandboxové originy; `region`
je jeden z `sk`, `cz`, `sandbox-sk`, `sandbox-cz`. Autentizační údaje
`email`, `api_key` a povinný `pii_key` přicházejí pouze v podepsaném body
svázaném s workspace a instalací.

Runtime neposkytuje vytváření nebo úpravu dokladů, platby, PDF ani odesílání
e-mailů. Každý request používá pevnou GET cestu, zakazuje redirecty a proxy
z prostředí, omezuje odpověď na 1 MiB a vrací nejvýše 256 KiB konzervativně
pseudonymizovaných dat. `test_connection` načte jednu položku adresáře.

Mock testy nenahrazují přejímku skutečného sandboxového účtu. Platformní
provisioning, replay ACL, mTLS/egress, katalog a aktivace jsou samostatná
integrační dodávka.

## Nástroje

| nástroj | popis |
|---|---|
| `list_invoices` | Stránka faktúr a ich stavov. |
| `get_invoice` | Detail faktúry podľa ID. |
| `list_clients` | Stránka klientov v adresári. |
| `get_client` | Detail klienta podľa ID. |
| `list_expenses` | Stránka nákladov a ich stavov. |
| `get_expense` | Detail nákladu podľa ID. |

## Lokální použití

Credentials patří do JSON souboru čitelného jen vlastníkem (`chmod 600`);
`pii_key` je volitelný — bez něj jsou pseudonymizované tokeny náhodné pro každý běh:

```json
{"region": "…", "email": "…", "api_key": "…", "pii_key": "<náhodný řetězec ≥ 32 znaků>"}
```

```sh
python -m connector_superfaktura local tools
printf '%s' '{}' | OPENMCP_LOCAL_CREDENTIALS_FILE=$HOME/.openmcp/superfaktura.json \
  python -m connector_superfaktura local call --privacy balanced list_invoices
```

Jako stdio MCP server pro Claude Desktop / Claude Code: `python -m connector_superfaktura mcp`
nebo `docker run -i … openmcp-connector-superfaktura mcp`; konfiguraci klienta vypíše
`python3 scripts/mcp_config.py superfaktura --credentials $HOME/.openmcp/superfaktura.json`.
Argumenty nástroje se čtou jako JSON ze stdin, `--privacy strict|balanced|plain` volí
ochranu dat. Podrobnosti: [docs/local-cli.md](../docs/local-cli.md),
[docs/mcp-stdio.md](../docs/mcp-stdio.md).
