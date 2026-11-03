# iptv-italia-builder

Aggregatore di fonti IPTV italiane. Produce una playlist unica con numerazione LCN **derivata solo dai PDF ufficiali del Ministero (MIMIT)**.

## Flusso

1. `src/lcn_official.py` – legge **prioritariamente i PDF archiviati nel repository** (`config/lcn_pdfs/`), li classifica **dal contenuto** (non dal nome file), sceglie il più recente e ricostruisce `state/lcn_official.json` solo se la tabella cambia. Impronta in `state/official_fingerprint.json`; variazioni in `output/lcn_changes.json`. Il sito MIMIT è interrogato solo a scopo diagnostico (timeout breve, non bloccante): segnala in `lcn_changes.json` (`remote_check`) se esiste un PDF più recente, ma non modifica mai la numerazione. Se in una cartella non ci sono PDF, si ripiega sul download dal sito.
2. `src/downloader.py` – scarica le sorgenti stream di `config/sources.yaml` (incluso LiveTVCollector).
3. `src/parser.py` – normalizza nomi, alias e duplicati; gli identificativi tecnici (Pluto, TOPTiVU, hash, `IT…`) sono sostituiti dal nome editoriale; se irrisolvibili il canale è escluso.
4. `src/prober.py` – verifica i flussi e calcola il punteggio di qualità.
5. `src/selector.py` – abbina i canali alla tabella ufficiale (nazionale con precedenza assoluta sulla regione), sceglie il miglior flusso, ordina prima i numerati per LCN, poi gli altri in ordine alfabetico. Output: `output/final_playlist.m3u` e `output/final_playlist_full.m3u`.

**Scelta del flusso:** vale il punteggio di qualità, con preferenza per i domini ufficiali degli editori (`config/official_domains.yaml`): un flusso ufficiale verificato con score entro il 10% del migliore vince; i flussi di domini terzi hanno una penalità del 10% sullo score.

DTTI/Libero non influenzano più la numerazione.

**EPG.** Le playlist finali riportano nell'header `x-tvg-url` e `url-tvg` con tutti i riferimenti XMLTV delle sorgenti (ordinati per `epg_priority` in `config/sources.yaml`; per FreeTV solo le guide italiane, filtrate da `epg_url_filter`). Per ogni canale mantengono `tvg-id`, `tvg-name`, `tvg-logo`, `tvg-chno` (LCN) e `group-title`. `tvg-id` e `group-title` vengono dalla sorgente con `epg_priority` più alta che li ha (il gruppo generico "Italy" è usato solo se non c'è di meglio). Sono riconosciute anche le varianti `tvgid`, `vg-id`, `tvg_logo`.

**Diagnostica ffprobe.** Il workflow stampa `which ffprobe` e `ffprobe -version`, poi un riepilogo (`src/probe_summary.py`: analizzati, validi, falliti, scartati, cache hit) e pubblica `probe_stats.json` e `probe_results.json` negli artifact.

## Configurazione

- `config/official_sources.yaml` – scope (nazionale, regioni), cartelle PDF, pagine MIMIT diagnostiche, soglie di validazione
- `config/user_region.yaml` – regione della playlist
- `config/official_domains.yaml` – whitelist domini ufficiali, margine e penalità
- `config/sources.yaml` – sorgenti stream e priorità loghi
- `config/channel_aliases.json` – alias tra chiavi canale
- `config/official_name_aliases.json` – nomi ufficiali → chiave canale

## PDF ufficiali e numerazione regionale

I PDF LCN sono nel repository, così la pipeline non dipende dalla raggiungibilità del sito del Ministero:

```
config/lcn_pdfs/
  nazionale/                      LCN_PUBBLICAZIONE_10.12.2025.pdf
  regioni/
    sicilia/                      LCN_AT17.pdf
    <regione>/                    ...
```

Il nome del file è irrilevante: in ogni cartella vale il PDF valido con data più recente (metadati o nome). Per aggiornare la numerazione basta sostituire/aggiungere il PDF nella cartella e fare commit.

**Selezionare la propria regione.** Modificare `config/user_region.yaml`:

```yaml
region: sicilia
```

La numerazione nazionale ha sempre precedenza; si aggiunge quella della sola regione scelta.

**Aggiungere una nuova regione** (es. Lombardia, area tecnica 1):

1. scaricare dalla pagina *Banda 700 - FSMA* il PDF della propria area tecnica e metterlo in `config/lcn_pdfs/regioni/lombardia/`;
2. aggiungere in `config/official_sources.yaml`, sotto `scopes`:

   ```yaml
   lombardia:
     kind: area_tecnica
     area_tecnica: 1        # numero AT che compare nel PDF
     region: Lombardia      # nome che compare nel PDF
     local_dir: regioni/lombardia
     page: "https://www.mimit.gov.it/it/comunicazioni/televisione/fornitori-di-servizi-di-media-audiovisivi-fsma/banda-700-fsma"
     hints: ['AT\s*-?\s*0*1\b', 'lombardia']
     min_rows: 20
     max_drop_pct: 30
   ```
3. impostare `region: lombardia` in `config/user_region.yaml`.

**Rigenerare la playlist** con numerazione regionale: `python src/main.py` (oppure in GitHub Actions dal workflow). Senza `ffprobe` locale i flussi sono valutati solo dai manifest. Cambiare regione rimuove dalla tabella `state/lcn_official.json` le voci delle altre regioni. Se il nome della regione non è configurato la pipeline si ferma con un errore esplicito. Per saltare il controllo diagnostico sul sito MIMIT: `LCN_REMOTE_CHECK=0`.

## Uso

```
pip install -r requirements.txt
python src/main.py
```

Test: `pip install -r requirements-dev.txt && python -m pytest tests`

La workflow `.github/workflows/nightly.yml` esegue tutto alle 04:00 Europe/Rome; la tabella LCN viene ricostruita solo se i documenti ufficiali cambiano. Dettagli in `docs/PIANO_TECNICO_LCN_UFFICIALE.md`.
