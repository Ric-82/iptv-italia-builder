# iptv-italia-builder

Aggregatore di fonti IPTV italiane. Produce una playlist unica con numerazione LCN **derivata solo dai PDF ufficiali del Ministero (MIMIT)**.

## Flusso

1. `src/lcn_official.py` – scopre i PDF ufficiali dalle pagine MIMIT (national: *Banda 700 - FSMA nazionali*; Sicilia AT17: *Banda 700 - FSMA*), li classifica **dal contenuto** (non dal nome file), sceglie il più recente e ricostruisce `state/lcn_official.json` solo se la tabella cambia. Impronta in `state/official_fingerprint.json`; variazioni in `output/lcn_changes.json`. Se una fonte non è raggiungibile o la tabella è sospetta, resta la tabella precedente.
2. `src/downloader.py` – scarica le sorgenti stream di `config/sources.yaml` (incluso LiveTVCollector).
3. `src/parser.py` – normalizza nomi, alias e duplicati; gli identificativi tecnici (Pluto, TOPTiVU, hash, `IT…`) sono sostituiti dal nome editoriale; se irrisolvibili il canale è escluso.
4. `src/prober.py` – verifica i flussi e calcola il punteggio di qualità.
5. `src/selector.py` – abbina i canali alla tabella ufficiale (nazionale con precedenza assoluta sulla Sicilia), sceglie il miglior flusso, ordina prima i numerati per LCN, poi gli altri in ordine alfabetico. Output: `output/final_playlist.m3u` e `output/final_playlist_full.m3u`.

DTTI/Libero non influenzano più la numerazione.

## Configurazione

- `config/official_sources.yaml` – pagine MIMIT, soglie di validazione, hint per le aree tecniche
- `config/sources.yaml` – sorgenti stream e priorità loghi
- `config/channel_aliases.json` – alias tra chiavi canale
- `config/official_name_aliases.json` – nomi ufficiali → chiave canale

## Uso

```
pip install -r requirements.txt
python src/main.py
```

Test: `pip install -r requirements-dev.txt && python -m pytest tests`

La workflow `.github/workflows/nightly.yml` esegue tutto alle 04:00 Europe/Rome; la tabella LCN viene ricostruita solo se i documenti ufficiali cambiano. Dettagli in `docs/PIANO_TECNICO_LCN_UFFICIALE.md`.
