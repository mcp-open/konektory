# Přidání konektoru

Konektor je provider adaptér nad společným SDK (`sdk/`): pevný HTTPS origin,
read-only nástroje s uzavřenými Pydantic schématy, credentials jen z
invocation kontextu a výstup vždy obalený `private_envelope`.

## Vytvoření a registrace

```sh
python3 scripts/new_connector.py mujprovider --name "Můj provider" --port 8135
python3 scripts/connector_inventory.py register mujprovider
python3 scripts/connector_inventory.py check
make test-connector CONNECTOR=mujprovider
```

Generátor vytvoří balíček, manifest, Dockerfile, schémata, handler a testy.
Handler je záměrně nedokončený a vrací bezpečnou chybu; nevolá smyšlené API
a nepředstírá úspěšné připojení. Existující adresář se nepřepisuje. Po
násilném ukončení může zůstat nekompletní adresář; další běh jej odmítne
přepsat a registrace neúplnou strukturu nepřijme.

`connectors.list` je jediný registr konektorů pro testy a build. Kontrola
odmítne adresář s `connector.yaml`, který v registru chybí, i neplatný
registr. Port je jen vývojová hodnota pro samostatný běh runtime.

## Dokončení adaptéru

1. Podle oficiální dokumentace providera doplňte jen read-only operace a
   pevné omezení egressu. Klient nesmí přijímat libovolnou URL z tool
   arguments ani z credentials (výjimkou je validovaný tenant/instance host).
2. Pydantic schémata určují vstupy; manifest `connector.yaml` generuje
   `scripts/manifests.py` a CI odmítá drift. Každé jméno nástroje musí být
   ve `SPECS`, v manifestu i v testech.
3. Credentials zpracovávejte pouze přes SDK `credentials()`; `pii_key` je
   povinný. Žádné logování credentials ani provider payloadů. Pokud generické
   pole nese osobní údaj podle evidence (např. `nazev` partnera v adresáři),
   předejte `private_envelope(..., personal_fields=…)`.
4. Testujte se syntetickým providerem (`httpx.MockTransport`): úspěch, chybný
   vstup, cizí instalaci, provider 401/403/429/5xx, timeout, stránkování,
   neplatnou odpověď a zákaz nechtěných zápisů i při chybě nebo redirectu.
   Mock testy nenahrazují přejímku skutečného účtu — zapište, co bylo ověřeno
   naživo, do README konektoru.
5. Spusťte `make test` (lint, typy, testy všech balíčků, manifesty) a
   `make build` (obrazy + non-root smoke).

`make test-connector CONNECTOR=<slug>` je rychlá vývojová kontrola SDK a
jednoho adaptéru; před pull requestem spusťte celý `make test`.

## Co konektor sám nezajišťuje

Repo obsahuje jen adaptéry a lokální CLI/stdio MCP režim. Vícenájemný
hostovaný provoz (OAuth, tenant policy, audit, replay ochrana, provisioning
credentials) je věcí hostující platformy, která konektor nasazuje.
