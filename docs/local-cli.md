# Lokální použití konektorů bez OpenMCP

Každý registrovaný konektor lze spustit přímo z jeho image nebo Python
balíčku bez MCP, OpenMCP core, podpisového klíče a Valkey:

```sh
python -m connector_<slug> local tools                 # seznam read-only nástrojů + JSON schémata
printf '%s' '{"ico":"27074358"}' | python -m connector_ares local call ares_subjekt_lookup
```

Argumenty nástroje se čtou jako jeden JSON objekt ze standardního vstupu
(max. 64 KiB), takže se nedostanou do historie shellu. Výsledek je jeden JSON
objekt na stdout (`{"ok": true, "result": …}`); bezpečně formulovaná chyba jde
jako JSON na stderr s nenulovým exit kódem (2 = neplatný vstup/nástroj,
1 = ostatní). Spouštějí se pouze nástroje označené `read_only`.

## Credentials soukromých providerů

Konektory s `requires_secret` (všechny kromě ARES) čtou credentials ze souboru
`OPENMCP_LOCAL_CREDENTIALS_FILE`:

- absolutní cesta k běžnému souboru (ne symlink) do 64 KiB, práva `0600`
  (skupina/ostatní bez přístupu; jinak konektor odmítne číst);
- JSON objekt neprázdných řetězců s klíči přesně podle `credentials` v
  `connector.yaml` daného konektoru (např. Freelo: `email`, `api_key`;
  Daktela: `instance_url`, `access_token`);
- `pii_key` je volitelný: bez něj se pro každý běh vygeneruje náhodný klíč,
  takže pseudonymizované tokeny nejsou mezi voláními stabilní. Pro stabilní
  tokeny uveďte vlastní řetězec o délce ≥ 32 bajtů.

Tajemství se nikdy nepředávají v argumentech procesu ani v hodnotách
proměnných prostředí. Credentials jsou uvnitř procesu svázané s lokální
instalací `<slug>/local/local`, stejně jako v runtime by je odmítl cizí
`secret_ref`.

```sh
umask 077
printf '%s' '{"email":"jan@firma.cz","api_key":"…"}' > ~/.openmcp/freelo.json
printf '%s' '{"page":1}' | OPENMCP_LOCAL_CREDENTIALS_FILE=~/.openmcp/freelo.json \
  python -m connector_freelo local call list_tasks
```

Z Docker image běží proces jako UID 10001; soubor s credentials proto
připojte read-only a spusťte kontejner pod vlastním UID, aby byl `0600`
soubor čitelný:

```sh
docker build -f freelo/Dockerfile -t openmcp-connector-freelo:local .
printf '%s' '{"page":1}' | docker run --rm -i --user "$(id -u):$(id -g)" \
  -e OPENMCP_LOCAL_CREDENTIALS_FILE=/run/creds.json \
  -v "$HOME/.openmcp/freelo.json:/run/creds.json:ro" \
  openmcp-connector-freelo:local local call list_tasks
```

## Režimy ochrany dat

Výchozí `strict` odpovídá dosavadnímu výstupu: `private_envelope`
pseudonymizuje názvy, volné texty a neznámá pole. Lokální volání přijímá
`--privacy strict|balanced|plain`. `balanced` ponechá běžná obchodní data
čitelná, ale pseudonymizuje známá PII pole, person/contact/address větve,
volné texty a vložené kontakty. `plain` pseudonymizaci provider payloadu vypne.

Původní `local call --plain <nástroj>` zůstává aliasem pro
`--privacy plain`. Hodnoty shodné s credentials jsou maskované ve všech
režimech a limity hloubky a velikosti odpovědi platí dál. Režim je uložený
v lokálním invocation kontextu, ne v globálním stavu procesu. Hostovaný HTTP
runtime přijímá režim pouze v podepsaném `runtime_flags` od platformního core.

## MCP server

Stejný kontrakt credentials používá i `python -m connector_<slug> mcp`, který
konektor vystaví jako stdio MCP server pro Claude Desktop/Claude Code — viz
[mcp-stdio.md](mcp-stdio.md).

## Co lokální režim není

- `local` není MCP server; pro MCP klienty existuje samostatný stdio režim
  `mcp` (viz výše). Veřejný, vícenájemný MCP/OAuth obsluhuje Go core platformy.
- Neobsahuje tenant izolaci, tool policy, audit ani replay ochranu platformy;
  je to přímé volání providera pod vlastní odpovědností uživatele.
- `python -m connector_<slug>` bez argumentu dál spouští interní runtime
  server s plnou bezpečnostní konfigurací; jiný první argument než `local`
  je odmítnut.

Kontrakt hlídají `sdk/tests/test_local_cli.py` (soubor s credentials a všechny
tři privacy režimy) a `tests/test_local_cli_entrypoints.py`
(každý registrovaný konektor vypíše nástroje shodné s manifestem a bez
credentials selže bezpečně a offline).
