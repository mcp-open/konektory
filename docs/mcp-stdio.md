# Konektor jako lokální MCP server (stdio)

Každý registrovaný konektor lze spustit jako **stdio MCP server** pro Claude
Desktop, Claude Code nebo jiného MCP klienta — bez OpenMCP core, OAuth,
podpisového klíče a Valkey:

```sh
python -m connector_<slug> mcp [--privacy strict|balanced|plain]
```

Server mluví JSON-RPC 2.0 po řádcích na stdin/stdout (MCP transport `stdio`)
a implementuje jen `initialize`, `ping`, `tools/list` a `tools/call`. Verze
protokolu: `2026-07-28`, `2025-11-25`, `2025-06-18`, `2025-03-26`,
`2024-11-05` — server vrátí verzi klienta, pokud ji podporuje, jinak nejnovější.
Každý nástroj má `readOnlyHint: true`; `tools/call` vrací výsledek jako
textový JSON (a `structuredContent` u protokolů ≥ 2025-06-18); chyby providera
jsou výsledky s `isError: true`, ne chyby protokolu. Dávky JSON-RPC jsou
odmítnuté, řádek je omezen na 1 MiB.

Credentials a privacy režimy fungují stejně jako v [lokálním CLI](local-cli.md):
soukromé konektory čtou `OPENMCP_LOCAL_CREDENTIALS_FILE` (owner-only JSON,
klíče podle `connector.yaml`) a bez něj server neodstartuje (`credential_invalid`
na stderr, exit 1). `--plain` zůstává aliasem pro `--privacy plain`.

## Docker

Image každého konektoru umí totéž: `docker run -i … <image> mcp`. Proces
v image běží jako UID 10001, proto se soubor s credentials připojuje read-only
a kontejner spouští pod vlastním UID:

```sh
docker build -f freelo/Dockerfile -t openmcp-connector-freelo:local .
docker run -i --rm --network bridge --user "$(id -u):$(id -g)" \
  -e OPENMCP_LOCAL_CREDENTIALS_FILE=/run/openmcp/credentials.json \
  -v "$HOME/.openmcp/freelo.json:/run/openmcp/credentials.json:ro" \
  openmcp-connector-freelo:local mcp
```

`scripts/mcp_config.py` vygeneruje konfiguraci klienta (bez tajemství, jen
cesta k souboru):

```sh
python3 scripts/mcp_config.py freelo --credentials ~/.openmcp/freelo.json          # Claude Desktop JSON
python3 scripts/mcp_config.py freelo --credentials ~/.openmcp/freelo.json --python # bez Dockeru
python3 scripts/mcp_config.py ares --claude-code                                   # `claude mcp add-json …`
```

Claude Desktop: vložte výstup do `claude_desktop_config.json`
(`~/Library/Application Support/Claude/` na macOS, `%APPDATA%\Claude\` na
Windows). Claude Code: spusťte vypsaný příkaz `claude mcp add-json`.

## Co to není

- Není to platformní MCP endpoint: chybí tenant izolace, tool policy, audit,
  replay ochrana a OAuth. Je to přímé volání providera na stroji uživatele.
- Server nevyžaduje síť k inicializaci; síť potřebují až volání nástrojů.
- Výchozí spuštění image bez argumentu zůstává interní runtime OpenMCP.

Kontrakt hlídají `sdk/tests/test_mcp_stdio.py` (handshake, verze, výpis,
volání, chyby, guardy protokolu, credentials a privacy režimy) a
`tests/test_local_cli_entrypoints.py` (každý registrovaný konektor projde
`initialize` + `tools/list` a nástroje odpovídají manifestu).
