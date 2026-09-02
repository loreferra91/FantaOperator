# FantaOperator

Web app Streamlit **locale e monoutente**, con SQLite e assistente deterministico senza chiavi AI.
Rosa, stime di formazione, asta e scambi iniziali sono dimostrativi, non dati Serie A aggiornati.
Non esporre l'app a Internet senza autenticazione e isolamento di dati e connessioni.

## Avvio

```bash
python3 -m pip install -r requirements.txt
python3 -m streamlit run app.py --server.address localhost
```

Apri [FantaOperator](http://localhost:8501). DB: `data/fantaoperator.db`; percorso alternativo tramite `FANTAOPERATOR_DB`.

## Collector: stato reale

Codice originale: nessuno dei repository GitHub discussi è installato o copiato.

| Modulo | Funzione / limite |
| --- | --- |
| official_votes.py | XLSX/CSV/JSON, validazione periodo/redazione, eventi e S.V. Testato con fixture sintetiche, non con l'export autenticato della tua lega. |
| updater.py | Download del feed esplicito, import atomico, rettifiche, errori e worker. Condiviso da UI e comandi. |
| vote_store.py | Archivi isolati per lega, stagione, provider, redazione, giornata. |
| league_scraper.py | Confine sicuro per export privati JSON normalizzati. **Non è un parser validato della API privata Fantacalcio.it**; rose/formazioni/risultati non sono collegati alla UI. |

**Nessun feed live è configurato.** La pagina pubblica voti restituisce HTML, non un export JSON/CSV/XLSX.
Serve un endpoint/export autorizzato e la verifica del formato. Il collector non cerca genericamente sul Web,
non aggira login e non estrae cookie dal browser. Il nome “Fantacalcio.it” in un JSON non certifica la provenienza.

## Import e rettifiche

In **Impostazioni** scegli provider, redazione esatta, stagione e regolamento.
In **Voti & dati** seleziona giornata, carica il file e conferma la sua corrispondenza.
La conferma si azzera per ogni nuovo file o cambio di contesto della lega/giornata.
L'import è **IMPORT_LOCALE, non verificato sul Web**. Metadati incompatibili vengono comunque rifiutati.

Reimportare aggiorna i giocatori presenti e registra valori precedenti/nuovi. File identico = nessuna modifica.
Una riga assente non cancella un voto: per ritirarlo invia esplicitamente `vote: null`.
Righe non riverificate conservano il proprio timestamp. “Acquisito ora” non significa “pubblicato ora”:
non è ancora implementato un ordinamento per revisione/timestamp del provider.

Cambiare stagione o redazione separa gli archivi senza cancellarli.
Le vecchie tabelle `matchday_records` e `sync_runs` restano recuperabili nel DB:
essendo prive di stagione/redazione, gli eventuali vecchi export vanno reimportati con periodo confermato.

### XLSX supportato

Intestazioni esplicite `Nome/Giocatore/player`, `Voto/vote`, `Ruolo/role`, `Squadra/team` e campi del modello CSV.
Supportate intestazioni ripetute per squadra e abbreviazioni `Gf, Gs, Rp, Amm, Esp, Au, Ass`.
Con più fogli, uno deve avere nome identico alla redazione configurata.
Formule, intestazioni duplicate e abbreviazioni ambigue non zero (es. `Rf, Rs`) vengono rifiutate.
Anche colonne sconosciute, campi JSON duplicati e righe CSV con un numero errato di colonne sono rifiutati.
Usare intestazioni esplicite. **Compatibilità universale con gli export Fantacalcio 2026 non verificata.**

### JSON normalizzato — schema 1

Esempio sintetico:

```json
{
  "schema_version": 1,
  "provider": "Fantacalcio.it",
  "edition": "Redazione Fantacalcio",
  "season": "2026-27",
  "matchday": 3,
  "records": [
    {
      "player": "Esempio Giocatore",
      "role": "ATT",
      "team": "Squadra esempio",
      "vote": 6.5,
      "status": "PROVVISORIO",
      "goals": 1,
      "penalties_scored": 1,
      "penalties_missed": 0,
      "assists": 0
    }
  ]
}
```

Eventi facoltativi (omessi = zero): `goals, assists, yellow_cards, red_cards, own_goals, goals_conceded,
penalties_saved, penalties_scored, penalties_missed, clean_sheet, custom_bonus, custom_malus`.

`goals` include i rigori: il bonus rigore sostituisce il bonus gol, senza doppio conteggio.
Clean sheet deve essere esplicito. `null` o `S.V.` produce un fantavoto nullo, mai zero.
Sostituzioni, voti d'ufficio, assist a pesi multipli e modificatori di squadra non sono automatici.
Duplicati/omonimi sono rifiutati. Stato assente = PROVVISORIO; DEFINITIVO deve essere dichiarato.
Un insieme misto assume lo stato meno consolidato.

## Aggiornamento automatico

Il feed deve dichiarare `provider, edition, season, matchday` (in CSV, colonne ripetute).
L'URL può contenere `{season}` e `{matchday}`. Non inserire credenziali negli URL.
Il DB locale non è cifrato e conserva l'URL configurato; query e frammenti sono rimossi dai log e dalla visualizzazione.

- Streamlit aperto: controllo ogni 30 secondi, download all'intervallo scelto (minimo 5 minuti).
- Browser chiuso: avvia separatamente il worker con lo stesso DB e intervallo abilitato:

```bash
python3 -m fantaoperator.updater --watch
```

Il processo deve restare attivo e il Mac acceso. Non è installato come servizio, né avviato di default.
Segue la giornata corrente configurata, non scopre l'ultima giornata o tutte le rettifiche storiche.

Controllo singolo, anche su giornata precedente:

```bash
python3 -m fantaoperator.updater --once --league 1 --matchday 3
```

Uscita 1 se il feed manca o fallisce. Errori/HTML/login non alterano i dati validi.
Dopo un errore il worker rispetta l'intervallo: nessun retry aggressivo.
Lock interprocesso macOS/Linux condiviso da download e import manuali; transazioni SQLite per i salvataggi.
Se cambia l'URL del feed durante il download, la risposta viene scartata.

`/VOTI, /VOTILIVE, /AGGIORNAVOTI` e richieste naturali sui voti tentano il feed prima del DB.
Se falliscono, lo dichiarano; non presentano vecchie rettifiche come nuove.
Non vengono consultati automaticamente Giudice Sportivo o altre fonti giornalistiche.

## Accesso privato

`league_scraper.fetch_private_export` accetta solo HTTPS sul dominio esatto `leghe.fantacalcio.it`.
Sessione fornita esplicitamente tramite `FANTACALCIO_COOKIE` o file locale con permessi 600.
Non incollare cookie in chat, Git o argomenti shell. `fantacalcio_cookie.txt` è ignorato da Git.
Cookie mai nei log/DB; tutti i redirect autenticati sono bloccati.

L'adattatore accetta schema 1 con `league, season, matchday` e liste `rosters, lineups, results`.
Non valida ancora il contenuto interno di queste liste e non conosce lo schema effettivo del provider.
Serve un esempio autorizzato del formato reale prima di considerare l'integrazione completata.

## Verifica e riferimenti

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q app.py fantaoperator
```

Test con fixture sintetiche e DB temporaneo. La prova autenticata sulla lega reale resta da fare.

Repository esaminati, senza riuso:
- [gingardia/fantacalcio-scraper](https://github.com/gingardia/fantacalcio-scraper): cerca soprattutto formazioni recuperate dal sistema; non connettore completo.
- [riccardopiola/fantacalcio-downloader](https://github.com/riccardopiola/fantacalcio-downloader): XLSX, dichiarato testato il 26 gennaio 2023.
- [piopy/fantacalcio-py](https://github.com/piopy/fantacalcio-py): pipeline scouting FPEDIA/FSTATS.
- [mastrogpt/fantacalcio-project](https://github.com/mastrogpt/fantacalcio-project): progetto distinto, non incluso.

La [pagina voti Fantacalcio.it](https://www.fantacalcio.it/voti-fantacalcio-serie-a) distingue Redazione Fantacalcio,
Voto Statistico e Voto Italia: la redazione fa quindi parte della chiave dei dati.
