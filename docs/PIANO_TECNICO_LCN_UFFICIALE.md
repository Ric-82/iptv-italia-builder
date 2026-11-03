# Piano tecnico – LCN ufficiale Ministero + nuova sorgente LiveTV + eliminazione ID tecnici

Stato: **BOZZA DA APPROVARE – nessuna modifica al codice è stata fatta.**

## 0. Stato attuale (verificato sul repository)

| Area | Oggi |
|---|---|
| Numerazione | [selector.py](../src/selector.py) legge `config/lcn_nazionale.json` (~209 righe, tabella manuale `CHIAVE → numero`). `tvg-chno` delle playlist è già ignorato. |
| Raccolta | [main.py](../src/main.py) + [downloader.py](../src/downloader.py) scaricano le sorgenti di `config/sources.yaml` in `cache/playlists/<nome>.m3u`. |
| Normalizzazione | [parser.py](../src/parser.py): `raw_key` / `normalize_key` (rimozione suffissi HD/FHD/4K…), `KEY_ALIASES` hard-coded, `ITALIAN_PLAYLISTS` / `EPG_PLAYLISTS` hard-coded. Output `output/canonical_channels.json`. |
| Alias | `config/channel_aliases.json` (manuale), applicato **solo in selector.py** (dopo il probing). Contiene ad es. `IT2900001B5 → PLUTOBBCSERIES`. |
| Qualità/probe | [prober.py](../src/prober.py): `compute_score = w*h + bitrate`, cache 24 h (`CACHE_MAX_AGE`), `TIMEOUT = 8`, risoluzione Mediapolis/LA7. Ranking `best/backup1/backup2/fallback` in `probe_results.json`. **Questi criteri restano invariati.** |
| Ordinamento | numerati per `tvg_chno`, poi non numerati per nome (selector.py). |
| Scheduling | `.github/workflows/` è **vuota**: oggi non esiste alcuna rigenerazione automatica delle 04:00. Va creata. |
| ID tecnici | `output/unresolved_technical_channels.json` elenca i canali con ID tipo `ITBC11000047F`, `ITAJ38000055E`, ecc. che finiscono nella playlist con il codice come nome. |

Limite di questa analisi: gli URL esatti delle pagine/PDF del Ministero (e di `LiveTV.m3u`) **non sono stati verificati** (i riferimenti 【…】 nel messaggio non contengono link e i tentativi su URL ipotizzati hanno dato 404). Vanno forniti/confermati (vedi §10); il piano è volutamente indipendente dai nomi file.

## 1. Architettura target

```
config/official_sources.yaml ─┐
                              ▼
 [A] lcn_official.py  ── scopre PDF ── scarica ── hash ── (cambiato?) ──► parse ──► build tabella
        │                                   │ no → riusa state/lcn_official.json (invariato)
        ▼
 state/lcn_official.json  (tabella LCN + metadati sorgente, versionata in git)
        │
 [B] downloader (sources.yaml + LiveTVCollector) ─► parser.py (alias/ID tecnici/dedup) ─► prober.py (invariato)
        │
 [C] selector.py: join tabella LCN ⇄ canali canonici ► scelta flusso ► ordinamento ► playlist
```

Nuovi file: `src/lcn_official.py` (scoperta/parsing/diff), `src/channel_identity.py` (nomi editoriali/alias), `src/keys.py`, `config/official_sources.yaml`, `state/lcn_official.json`, `state/official_fingerprint.json`, `.github/workflows/nightly.yml`, `tests/`.
File rimossi: `config/lcn_nazionale.json` (sostituito dalla tabella generata).
Nuove dipendenze: `pypdf` (metadati/testo), `pdfplumber` (tabelle/coordinate), `beautifulsoup4` (parsing HTML pagina).

## 2. Individuazione automatica dei PDF ministeriali

L'**unico ancoraggio fisso** è l'URL delle due pagine (config, non codice):

```yaml
# config/official_sources.yaml
pages:
  national: { url: "<pagina Banda 700 - FSMA nazionali (2021)>" }
  regional: { url: "<pagina Banda 700 - FSMA>" }
sicily:
  area_tecnica: 17           # AT17 = Sicilia (dato fornito; verificato anche sul contenuto)
  markers: ["sicilia"]
thresholds: { min_rows_national: 100, max_drop_pct: 30 }
```

Algoritmo (uguale per le due pagine, nessun nome file hard-coded):

1. GET della pagina (User-Agent browser, retry/backoff, timeout 30 s). Si estraggono **tutti** i link a documenti: `href` che terminano in `.pdf` oppure con `Content-Type: application/pdf` (HEAD) o link `/documents/…/download`. Si risolvono gli URL relativi.
2. Per ogni candidato si scarica il file (cache in `cache/official/`) e lo si **classifica per contenuto**, non per nome:
   - estrazione testo pagine 1–2 (`pypdf`, fallback `pdfplumber`);
   - **Nazionale**: tabella LCN con colonne numero/denominazione, titolo “LCN”, assenza di marker di area tecnica, ≥ `min_rows_national` righe numerate, presenza di emittenti note (RAI, Mediaset, LA7…) come controllo di plausibilità;
   - **Sicilia (AT17)**: testo contiene “Area Tecnica 17”/“AT17” **oppure** “Sicilia” come intestazione; tabella LCN valida.
   - Altri PDF (altre aree tecniche, note, moduli) → scartati e loggati.
3. Tra i PDF che soddisfano la classe, si sceglie il **più recente** con questa gerarchia (il nome file è solo ultimo spareggio debole):
   1. data di pubblicazione/aggiornamento **dentro il documento** (pattern `gg.mm.aaaa`, “aggiornamento al…”);
   2. data nel testo del link/riga della pagina;
   3. metadati PDF (`/ModDate`, `/CreationDate`);
   4. `Last-Modified` HTTP;
   5. data nel nome file, poi ordine nella pagina.
4. **Validazione** del PDF scelto (sanity check §4). Se il candidato più recente fallisce si prova il successivo; se nessuno è valido → **fail-safe**: si mantiene la tabella precedente e si segnala l'errore (exit code ≠ 0 per avvisare, senza toccare i file).
5. Il risultato è registrato in `state/official_fingerprint.json` (URL scelto, titolo link, data dedotta, criterio usato, hash).

Se la pagina cambia struttura (nessun PDF trovato) → stesso fail-safe, nessuna modifica alla numerazione.

## 3. Rilevazione delle variazioni nel tempo

`state/official_fingerprint.json` per ciascun ambito (`national`, `sicily`):

```json
{ "national": { "url": "...", "sha256": "...", "etag": "...", "last_modified": "...",
                "doc_date": "2025-12-10", "table_sha256": "...", "rows": 0, "checked_at": "..." } }
```

Controllo a tre livelli (dal più economico):

1. **Pagina/elenco link**: se l'URL scelto è lo stesso e `ETag`/`Last-Modified` coincidono (GET condizionale) → nessun download.
2. **Hash SHA-256 dei byte del PDF** scelto (o URL diverso) → se invariato: STOP, nessuna ricostruzione.
3. **Hash della tabella normalizzata** (`table_sha256`, JSON canonico ordinato): se il PDF è cambiato ma la tabella è identica (ripubblicazione cosmetica) → si aggiorna solo il fingerprint, **la numerazione non cambia**.

Ricostruzione solo se cambia almeno un `sha256` di PDF **e** il `table_sha256` risultante. Il confronto avviene per ambito; l'unione viene comunque riscritta in modo deterministico.

Output del job: `state/lcn_changes.json` con diff (aggiunti / rimossi / rinumerati) e flag `changed: true|false`, usato dal workflow per decidere il commit.

## 4. Ricostruzione della tabella LCN ufficiale

1. **Estrazione righe**: `pdfplumber.extract_tables()`; fallback su parole con coordinate (raggruppamento per riga `y`, colonne per `x`) per PDF senza bordi. Il parser tollera intestazioni ripetute per pagina, celle multi-riga, spazi/NBSP.
2. Riga valida = numero intero (1–999) + denominazione non vuota (+ eventuale operatore, conservato come metadato).
3. **Normalizzazione nome ufficiale → chiave canonica** con le stesse funzioni di `parser.py` (`raw_key`/`normalize_key`, spostate in `src/keys.py`), così tabella e canali parlano lo stesso linguaggio.
4. **Sanity check** (se falliscono, ricostruzione annullata e resta la tabella precedente):
   - righe ≥ `min_rows_national`;
   - nessun numero duplicato nello stesso PDF (altrimenti riportato in `lcn_build_report.json`);
   - calo righe rispetto alla versione precedente > `max_drop_pct` → rifiuto + allarme;
   - presenza di canali “sentinella” (RAI 1/2/3, Rete 4, Canale 5, Italia 1, La7).
5. Scrittura **atomica** (file temporaneo + `os.replace`), JSON con chiavi ordinate, indentazione fissa, nessun timestamp nel corpo della tabella (stanno nel fingerprint) → output byte-identico a parità di input.

Formato `state/lcn_official.json`:

```json
{ "meta": { "national": {"source_sha256": "...", "doc_date": "..."}, "sicily": {"...": "..."} },
  "channels": { "RAI1": [ {"lcn": 1, "scope": "national", "official_name": "Rai 1"} ],
                "TGS":  [ {"lcn": 12, "scope": "sicilia", "official_name": "TGS"} ] } }
```

## 5. Integrazione numerazione nazionale + siciliana

- Le due sezioni sono unite in un'unica tabella con campo `scope`.
- **Precedenza**: un canale presente nel PDF nazionale usa il numero nazionale; i canali solo nel PDF siciliano usano il numero siciliano.
- **Collisioni di numero** (stesso LCN in nazionale e Sicilia, tipico degli slot regionali): non si arbitra in silenzio. Regola proposta: il nazionale vince, il regionale in collisione è scritto in `lcn_build_report.json` e nella playlist **non** riceve numero (finisce in coda). *Da confermare (Q2).*
- Un canale con più numeri (es. SD/HD) mantiene tutti i numeri nella tabella; ne viene usato uno solo (proposta: il più basso). *Da confermare (Q3).*
- **Matching** nome ufficiale ↔ canale aggregato, senza tabelle manuali, a livelli:
  1. chiave canonica identica;
  2. chiave identica dopo l'alias map **generata** (§7);
  3. match per token normalizzati solo se univoco e con soglia alta;
  4. ambiguità o assenza: **nessun numero**, voce in `lcn_unmatched_report.json` (sia “ufficiale senza flusso” sia “flusso senza ufficiale”).
- Le tabelle `dtti_name_mapping`, `libero_lcn_candidates`, `lcn_master_*`, `chno_*` ecc. restano **solo** come file diagnostici in `output/diagnostics/`; `selector.py` non li legge (un test fallisce se `selector.py` referenzia sorgenti LCN diverse da `state/lcn_official.json`).

## 6. Nuova sorgente LiveTVCollector (`LiveTV.m3u`)

- `config/sources.yaml`: nuova voce `LiveTVCollector` con `url` (da confermare) e campi dichiarativi:

```yaml
  - name: LiveTVCollector
    url: "<URL LiveTV.m3u>"
    include: all              # all | country_it
    logo_priority: 3
    epg_from_header: true
```

- `downloader.py`: stessa logica (salva `cache/playlists/LiveTVCollector.m3u`); aggiunta di retry/backoff e controllo che il contenuto inizi con `#EXTM3U`, per non sovrascrivere la cache con una pagina di errore.
- `parser.py`: `ITALIAN_PLAYLISTS`, `EPG_PLAYLISTS` e la regola speciale `FreeTV`/`tvg-country="IT"` sono letti da `include`/`epg_from_header` invece che da costanti. Il filtro `include` per LiveTV va verificato sul contenuto reale (se contiene canali non italiani: `country_it` + `group-title`).
- `selector.py`: `LOGO_PRIORITY` letto da `logo_priority` in config.
- Il parser già gestisce URL su righe successive e righe `#EXTVLCOPT/#KODIPROP`; si verifica se LiveTV usa header HTTP per-flusso, da propagare nella playlist.

## 7. Strategia definitiva contro gli identificativi tecnici (Pluto TV, TOPTiVU, …)

### 7.1 Rilevamento (`channel_identity.is_technical_id`)

Un nome è “tecnico” se corrisponde a: `^IT[A-Z0-9]{8,14}$` (es. `ITBC11000047F`), esadecimali/hash ≥ 16 caratteri, UUID, `^[0-9a-f]{24}$` (ObjectId Pluto), solo cifre, stringhe senza vocali con ≥ 3 cifre, nomi-macchina/slug con cifre finali lunghe, e voci già presenti in `unresolved_technical_channels.json`.

### 7.2 Risoluzione del nome editoriale (primo non-tecnico vince)

1. `tvg-name`;
2. nome dopo la virgola di `#EXTINF`, pulito da prefissi (`Pluto TV`), tag `[HD]`, `(IT)`;
3. nome dello **stesso flusso** in altre sorgenti (URL normalizzato senza token/query volatili);
4. nome dello stesso `tvg-id` in altre sorgenti;
5. stesso `tvg-logo` in altre sorgenti;
6. `display-name` XMLTV dagli EPG già raccolti (`epg_sources.json`) per quel `tvg-id`;
7. slug leggibile dell'URL (es. `chili-chilivelvet-1-it` → “Chili Velvet”) **solo** come ultima risorsa.

### 7.3 Unificazione tramite alias (generati, non manuali)

Grafo di identità con union-find: nodi = chiavi canoniche; archi tra nodi che condividono **URL**, **tvg-id**, **logo** o **nome editoriale normalizzato**. Ogni componente connessa = un canale; nome canonico = nome editoriale più frequente (a parità: priorità sorgente). Risultato in `output/alias_map.json` (generato a ogni run, ordinato). `config/channel_aliases.json` resta solo come **override manuali minimi** (vincono sulla mappa generata) e viene ripulito dagli ID tecnici ormai coperti.

L'unificazione avviene **in `parser.py`, prima del probing**: `prober.py` valuta una sola volta i flussi di un canale e il selector non applica più alias a posteriori.

### 7.4 Garanzia “mai in playlist”

- Nome in `#EXTINF` e `tvg-name` sono sempre il nome editoriale; l'ID tecnico non compare mai come testo visibile (proposta: `tvg-id` = chiave editoriale pulita).
- **Gate finale in `selector.py`**: un canale con nome finale ancora tecnico è escluso dalla playlist e scritto in `output/quarantine_technical.json` con le evidenze raccolte; il job stampa un warning. Test automatico: nessuna riga `#EXTINF` di `final_playlist*.m3u` soddisfa `is_technical_id`.
- `final_playlist_full.m3u` continua ad aggiungere `[Playlist]` al nome, ma il nome base è sempre editoriale.

## 8. Modifiche per file

| File | Modifiche |
|---|---|
| `config/sources.yaml` | + `LiveTVCollector`; campi `include`, `epg_from_header`, `logo_priority`. |
| `config/official_sources.yaml` (nuovo) | URL pagine Ministero, soglie, parametri Sicilia. |
| `config/lcn_nazionale.json` | **Eliminato**. |
| `config/channel_aliases.json` | Solo override manuali; rimossi ID tecnici ridondanti. |
| `src/lcn_official.py` (nuovo) | Scoperta PDF, classificazione per contenuto, fingerprint, parsing, build tabella, diff, scrittura atomica. |
| `src/keys.py` (nuovo) | `raw_key`, `normalize_key`, `canonical_key` estratti da `parser.py`. |
| `src/channel_identity.py` (nuovo) | `is_technical_id`, nome editoriale, union-find alias, `alias_map.json`. |
| `src/downloader.py` | Retry/backoff, validazione `#EXTM3U`, `datetime.utcnow()` → `datetime.now(timezone.utc)`. |
| `src/parser.py` | Config sorgenti da YAML; usa `keys.py`; integra `channel_identity` prima di scrivere `canonical_channels.json`; `tvg_chno` solo diagnostico; `KEY_ALIASES` hard-coded migrano in alias generati/override. |
| `src/prober.py` | **Nessuna modifica ai criteri.** |
| `src/selector.py` | Legge `state/lcn_official.json`; rimuove `apply_aliases` a posteriori; `LOGO_PRIORITY` da config; matching §5; ordinamento §9; gate ID tecnici; `lcn_assignment_report.json` con `source: official`. |
| `src/main.py` | Orchestrazione completa: `lcn_official` → download → parser → prober → selector (oggi solo download). |
| `requirements.txt` | + `pypdf`, `pdfplumber`, `beautifulsoup4` (+ `pytest` in dev). |
| `.github/workflows/nightly.yml` (nuovo) | Cron e commit condizionale (§11). |

## 9. Costruzione e ordinamento della playlist aggregata

1. Raccolta da **tutte** le sorgenti (incl. LiveTVCollector) → normalizzazione/alias/dedup (§7).
2. Numero assegnato **solo** se il canale è abbinato a una riga della tabella ufficiale (§5).
3. Scelta flusso: criteri esistenti (`compute_score` + probe; `best`/`backup1`/`backup2`/`fallback`).
4. Ordine finale:
   - **numerati**: per LCN crescente (chiave `int`); a parità nazionale prima di regionale, poi nome;
   - **non numerati**: in coda, alfabetico case-insensitive e senza accenti (`unicodedata` + `casefold`).
5. Output: `final_playlist.m3u` (flusso migliore) e `final_playlist_full.m3u` (con backup).

## 10. Domande aperte (servono prima dell'implementazione)

- **Q1** URL esatti delle due pagine Ministero e di `LiveTV.m3u`.
- **Q2** Collisioni numero nazionale/Sicilia: va bene “nazionale vince, regionale senza numero”, oppure la Sicilia ha priorità/range propri?
- **Q3** Canali con più numeri (SD/HD): usare il più basso?
- **Q4** Canali ufficiali senza flusso: solo report (proposta) o placeholder?
- **Q5** Canali con nome tecnico irrisolvibile: esclusi + quarantena (proposta) o tenuti con nome generico?
- **Q6** Ora 04:00 intesa come Europe/Rome (proposta, §11)?

## 11. Stabilità quando le fonti ufficiali non cambiano

- `state/lcn_official.json` è riscritto **solo** se `table_sha256` cambia; altrimenti non viene toccato.
- La numerazione non dipende da probe, playlist terze o ordine di download: è una funzione deterministica (tabella ufficiale ⨯ chiave canonica).
- Serializzazione JSON/M3U deterministica (chiavi ordinate, ordinamenti con chiave totale, nessun timestamp nel corpo).
- Fail-safe: errore di rete, pagina cambiata, PDF illeggibile o sanity check fallito → resta l'ultima tabella valida, nessuna sovrascrittura parziale.
- Workflow notturno: il cron di GitHub Actions è in UTC, quindi due schedule (`0 2 * * *` e `0 3 * * *`) con step iniziale che prosegue solo se in `Europe/Rome` sono le 04:xx. Passi: (1) `lcn_official` (ricostruzione condizionale), (2) download + parser + prober + selector (i flussi si aggiornano sempre, la numerazione no), (3) commit solo se `git diff` non è vuoto; il messaggio indica se la LCN è cambiata (`state/lcn_changes.json`).
- Mitigazione flapping probe: il probing può cambiare il flusso “migliore” ma mai numeri né ordine dei numerati; (opzionale) isteresi: `best` cambia solo se il nuovo score supera il precedente di una soglia.

## 12. Test e criteri di accettazione

`tests/` con fixture HTML/PDF sintetici:

- scoperta con PDF rinominati, ordine link diverso, più PDF datati, PDF di altre aree tecniche → viene scelto il PDF giusto;
- stesso PDF → `changed=false`, nessun file modificato (hash identici);
- PDF ripubblicato con tabella identica → numerazione invariata;
- PDF con modifiche → diff corretta, nessun numero duplicato;
- pagina/PDF non raggiungibile o corrotto → tabella precedente conservata;
- LiveTVCollector presente nel parsing; nessun `#EXTINF` finale con ID tecnico; alias (stessi flussi con ID diversi → un solo canale);
- ordinamento: numerati per LCN, poi alfabetico non numerati;
- `selector.py` non referenzia sorgenti LCN diverse dalla tabella ufficiale.

## 13. Fasi di implementazione proposte

1. `keys.py` + `channel_identity.py` + test (nessun impatto sull'output).
2. `lcn_official.py` + `official_sources.yaml` + test su fixture; eseguito in parallelo alla tabella manuale per confronto diagnostico.
3. LiveTVCollector + parametrizzazione sorgenti in parser/selector.
4. Switch del selector alla tabella ufficiale, rimozione `lcn_nazionale.json`, gate ID tecnici.
5. Workflow notturno, aggiornamento README, spostamento dei file diagnostici in `output/diagnostics/`.
