# Connector runtime SDK: distribuovaná replay ochrana

Každý podepsaný core → connector invocation token je jednorázový. Kontrola
podpisu, lifetime, audience, tenant/tool vazeb a přesného body digestu proběhne
dříve, než runtime atomicky claimne jeho `jti`. Produkční runtime používá
sdílený Valkey store, takže stejný token nelze provést ani přes jiný pod nebo po
restartu connectoru.

## Bezpečnostní invarianty

- Claim je jediný atomický příkaz `SET <key> 1 NX EX <zbývající TTL>`.
- Klíč je `replay:connector:<slug>:v1:<sha256(jti)>`; do Valkey se neukládá raw token,
  request body, tenant ani provider credential.
- TTL je odvozené z již ověřeného tokenu a je nejvýše 60 sekund.
- Valkey timeout nebo chyba poolu nikdy nepustí handler. Invocation vrátí
  bezpečné retryable `503`; již claimnutý token vrátí `401`.
- `/health/ready` vrátí `503`, pokud replay store neodpoví na bounded `PING`.
- Síťové volání běží mimo ASGI event loop. Connect/socket timeouty a pool mají
  tvrdé horní meze; klient nemá automatický retry.
- Produkční URL musí být `valkeys://`, ověřuje hostname a CA, vyžaduje klientský
  certifikát a minimálně TLS 1.2. URL nesmí obsahovat heslo ani query parametry.
- Process-local fallback se nezvolí implicitně a lze ho zapnout pouze s
  `APP_ENV=local|development|test`.

## Režimy ochrany provider dat

Privátní adaptéry podporují `strict`, `balanced` a `plain`; bez explicitního
režimu vždy použijí `strict`. Lokální CLI/stdio ukládá volbu do konkrétního
invocation kontextu. Hostovaný runtime přijímá `runtime_flags.privacy_mode`
pouze jako součást přesně podepsaného body od core. Neplatná hodnota selže
uzavřeně před provider voláním.

`balanced` zachovává běžná obchodní data, ale pseudonymizuje známá PII pole,
person/contact/address větve a volný text. `plain` vrací provider payload bez
pseudonymizace. Ani `plain` nikdy neodmaskuje hodnotu shodnou s credentialem,
nezmění limity odpovědi a nepovolí raw provider payload nebo exception v logu.

Klasifikace polí v `balanced` zná anglické i české/slovenské názvy providerů
(`jmeno`, `prijmeni`, `nazFirmy`, `firma`, `ulice`, `mesto`, `psc`, `tel`,
`mobil`, `kontakt*`, `fa*`, `buc`, `iban`, `createdBy`…, včetně ABRA variant
`pole@showAs`/`pole@ref`) a volné texty `popis`, `poznam`, `uvodTxt`,
`zavTxt`, `note`, `description`…. Ve volném textu neklasifikovaných polí se
maskují e-maily, IBAN, URL a telefonní čísla; heuristika telefonu vyžaduje
9–15 číslic a nezasahuje částky, ISO data, IČO ani kódy dokladů
(`FV1-000002/2025`). Allowlistované metriky, data (i s časovou zónou,
`2025-01-01+01:00`) a příznaky zůstávají čitelné v `strict` i `balanced`.

Generické pole může nést osobní údaj podle evidence (`nazev` partnera v
adresáři); adaptér, který evidenci zná, předá `private_envelope(...,
personal_fields=frozenset({"kod", "nazev"}))` a SDK je pseudonymizuje ve všech
režimech kromě `plain`.

## Produkční konfigurace

Connector pod vyžaduje:

| Proměnná | Požadavek |
| --- | --- |
| `OPENMCP_INTERNAL_TOKEN_FILE` | absolutní cesta k vlastnímu invocation signing key; regular non-symlink, 32–4096 bytů; přímý `OPENMCP_INTERNAL_TOKEN` je pouze XOR test kompatibilita |
| `OPENMCP_REPLAY_STORE` | přesně `valkey` |
| `OPENMCP_REPLAY_VALKEY_URL` | např. `valkeys://replay_ares@valkey.openmcp.svc:6379/0`; username musí odpovídat `replay_<slug>`, bez hesla a query |
| `OPENMCP_REPLAY_VALKEY_PASSWORD_FILE` | absolutní cesta k secret souboru, heslo 32–1024 bytů |
| `OPENMCP_REPLAY_TLS_CA_FILE` | absolutní cesta k trustovanému internímu CA bundle |
| `OPENMCP_REPLAY_TLS_CERT_FILE` | absolutní cesta ke klientskému certifikátu daného connector workloadu |
| `OPENMCP_REPLAY_TLS_KEY_FILE` | absolutní cesta k jeho privátnímu klíči |
| `OPENMCP_REPLAY_CONNECT_TIMEOUT_MS` | volitelné, default `100`, rozsah `10..1000` |
| `OPENMCP_REPLAY_SOCKET_TIMEOUT_MS` | volitelné, default `100`, rozsah `10..1000` |
| `OPENMCP_REPLAY_MAX_CONNECTIONS` | volitelné, default `16`, rozsah `1..64` |

Secret soubory mají být read-only volume/Secret mount, ne hodnoty v environment
nebo image. Certifikát musí mít server-auth trust chain a SAN přesně pro DNS v
URL; klientský certifikát musí být vydán interní CA pro connector replay
workload. Při rotaci certifikátu/hesla je nyní potřeba rolling restart podů.

SDK při chybějící, plaintext nebo neúplné konfiguraci odmítne runtime vytvořit.
Samotnou nedostupnost Valkey za běhu hlásí readiness a fail-closed invocation.

## Samostatná Valkey ACL identita

Každý connector musí mít vlastní identitu `replay_<slug>` a vlastní heslo, které
nesdílí s core session/rate/security klienty ani jiným connectorem. Minimální
ACL pro ARES je:

```text
user replay_ares on #<SHA256_HESLA> sanitize-payload ~replay:connector:ares:* resetchannels -@all +set +ping +hello +client|setname +client|setinfo
```

Dotykačka analogicky používá `replay_dotykacka` a pouze
`~replay:connector:dotykacka:*`. Uživatel nepotřebuje `GET`, `DEL`, `KEYS`,
`SCAN`, Lua, Pub/Sub ani administrační
příkazy. Valkey musí mít pro tuto ephemeral security databázi `maxmemory-policy
noeviction`: tiché vyhození replay markeru by porušilo one-use invariant. Při
vyčerpání paměti `SET` selže a runtime zůstane fail-closed.

V Kubernetes omezte NetworkPolicy tak, aby na TLS port Valkey mohly pouze core
a connector workloads, a aby connector pody nemohly navázat plaintext spojení
na starý port. TLS listener nesmí současně ponechat otevřený `port 6379` bez TLS;
použijte pouze `tls-port` (číslo portu může zůstat 6379).

## Explicitní lokální/test režim

Bez Valkey lze runtime spustit pouze explicitně:

```text
APP_ENV=development
OPENMCP_REPLAY_STORE=memory
OPENMCP_REPLAY_MEMORY_MAX_ENTRIES=10000
```

Paměťový store je bounded a thread-safe, ale negarantuje one-use přes více
procesů, podů nebo restart. Nesmí být použit ve staging/produkci. Testy mohou
také přímo předat `InMemoryReplayStore()` do `create_app(...)`, čímž je fallback
viditelný v call-site.

## Release ověření

```sh
ruff check sdk/src sdk/tests
mypy --config-file sdk/pyproject.toml sdk/src
pytest -q sdk/tests
```

K3s integrační gate musí navíc současně poslat identický platný token na dvě
repliky stejného connectoru a prokázat právě jeden `200` a jeden `401`. Při
odpojeném Valkey musí readiness i platná invocation vrátit `503` a handler se
nesmí spustit; po obnově má nový token projít právě jednou.
