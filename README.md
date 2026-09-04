# FantaOperator

Web app Streamlit per gestione rosa, formazioni e voti, con SQLite e assistente deterministico senza chiavi AI.
Rosa, stime di formazione, asta e scambi iniziali sono dimostrativi, non dati Serie A aggiornati.

## Versione online e dati personali

Entrypoint Streamlit Community Cloud: `app.py`, branch `main`, dipendenze in `requirements.txt`.
Senza `FANTAOPERATOR_DB`, ogni sessione browser ha un database temporaneo separato:
rosa, impostazioni e chat non sono condivise con altri visitatori. **Chiudere o ricaricare
la pagina può terminare la sessione.** Scarica il backup personale da **Impostazioni**
prima di uscire e ripristinalo alla visita successiva.

Il backup JSON conserva configurazione, rosa e formazioni di tutte le giornate/stagioni salvate.
Non comprende voti, chat, URL privati o automazioni: ricollega la fonte e risincronizza i voti.
Il ripristino valida l'intero file prima di sostituire i dati ed è atomico; scarica un backup
prima di importarne un altro. Limite file: 5 MB.

L'avviatore macOS imposta esplicitamente `FANTAOPERATOR_DB` e conserva il database sul Mac.
Questo percorso condiviso va usato solo per l'istanza locale monoutente su localhost.
L'app online non ha ancora account né archiviazione durevole su database remoto:
il backup è necessario per continuare in un'altra sessione/dispositivo.

## Flussi disponibili

- **Rosa**: aggiunta, modifica, rimozione e CSV con anteprima e validazione atomica.
  Nomi univoci, ruoli Classic, valori finiti, costi non negativi. Il modello CSV è scaricabile.
  Dai voti della giornata corrente puoi aggiungere nomi, ruoli e squadre alla rosa.
  Le valutazioni restano manuali: i voti non producono automaticamente previsioni di titolarità.
- **Formazione**: ottimizzazione Classic, scelta manuale, salvataggio per stagione/giornata,
  esportazione e conservazione dello snapshot anche dopo modifiche alla rosa.
- **Panoramica**: punteggio dell'undici salvato, copertura dei voti e totale parziale esplicito.
  Nessun voto abbinabile resta senza totale, non diventa zero. Nomi e squadre devono corrispondere.
- **Voti & dati**: import XLSX/CSV/JSON e lettura della pagina pubblica Gazzetta scelta per questa lega.
  Verifica reale del 4 settembre 2026: 315 record della giornata 2, stagione 2026/27,
  dei quali 301 con voto. La pagina contiene V, G, A, R, RS, AG, AM, ES e FV.
  L'app conserva il **FV Gazzetta** pubblicato e calcola separatamente il **FV lega**
  secondo i bonus configurati. Il valore pubblicato non viene usato per dedurre eventi mancanti.
- **Asta e mercato**: calcolatori sulle valutazioni inserite. Non registrano acquisti/scambi
  sulla piattaforma della lega. Gli indici euristici non sono probabilità di successo.

Per usare la propria lega: configura stagione, giornata, provider e bonus in **Impostazioni**;
importa la rosa in **Rosa**; collega la fonte in **Voti & dati**; salva l'undici in **Formazione**.
Mantra, sostituzioni, modificatori di squadra, import privato della lega e previsioni live
non sono implementati. Non vengono applicate regole Classic a una formazione Mantra.

## Avvio

Su macOS fai doppio clic su **Avvia FantaOperator.command** (disponibile anche sulla Scrivania di questo Mac).
Il browser si apre appena Streamlit risponde. Un secondo avvio riapre la stessa istanza senza duplicarla.
Lascia aperto il Terminale; premi **Ctrl+C** per arrestare l'app. Chiudere solo la scheda browser non arresta il server.
Serve Python 3.10 o successivo. Se mancano librerie, l'avviatore prepara `.venv` e installa `requirements.txt`:
solo questa preparazione richiede Internet; i voti remoti richiedono comunque una connessione.
Se la porta 8501 è occupata da un altro processo, viene scelta una porta libera fino alla 8520, senza fermare altri servizi.
Questo è un avvio locale, non una pubblicazione su Streamlit Community Cloud.

In alternativa, avvio manuale:

```bash
python3 -m pip install -r requirements.txt
FANTAOPERATOR_DB="$PWD/data/fantaoperator.db" python3 -m streamlit run app.py --server.address localhost
```

Apri [FantaOperator](http://localhost:8501). DB: `data/fantaoperator.db`; percorso alternativo tramite `FANTAOPERATOR_DB`.

## Collector: stato reale

Codice originale: nessuno dei repository GitHub discussi è installato o copiato.

| Modulo | Funzione / limite |
| --- | --- |
| gazzetta_votes.py | Pagina pubblica Gazzetta per stagione/giornata: voto, FV pubblicato, eventi, ruolo, squadra e identità del giocatore. Fonte predefinita del progetto. |
| official_votes.py | XLSX/CSV/JSON, validazione periodo/redazione, eventi e S.V. Testato con fixture sintetiche, non con l'export autenticato della tua lega. |
| public_votes.py | Pagina pubblica Fantacalcio.it: voti per redazione, eventi, stagione e giornata; nessun login. Contratto HTML verificato il 2 settembre 2026. |
| updater.py | Download del feed esplicito, import atomico, rettifiche, errori e worker. Condiviso da UI e comandi. |
| vote_store.py | Archivi isolati per lega, stagione, provider, redazione, giornata. |
| league_scraper.py | Confine sicuro per export privati JSON normalizzati. **Non è un parser validato della API privata Fantacalcio.it**; rose/formazioni/risultati non sono collegati alla UI. |

La fonte scelta per il progetto è la [pagina pubblica voti Gazzetta](https://www.gazzetta.it/calcio/fantanews/voti/serie-a-2026-27/).
Ogni nuova installazione e ogni database esistente vengono configurati una sola volta con
`Gazzetta / La Gazzetta dello Sport`; le modifiche manuali successive restano rispettate.
La pagina generale viene trasformata nell'URL esatto `giornata-N`, poi titolo, stagione,
giornata, intestazioni e identità dei calciatori vengono verificati prima dell'import.
Gli omonimi abbreviati sono distinti con l'identificativo Gazzetta presente nel link giocatore.

Il precedente connettore alla [pagina pubblica voti Fantacalcio.it](https://www.fantacalcio.it/voti-fantacalcio-serie-a) resta disponibile:
i numeri sono negli attributi `data-value`, non nel testo delle celle. Non serve il download XLSX riservato.
In **Voti & dati**, con provider Fantacalcio.it e URL vuoto, premi **Collega questa fonte ufficiale**;
oppure salva questo URL in **Impostazioni**. Poi scegli la giornata e **Verifica e sincronizza**.
Il collector usa il percorso pubblico `/voti-fantacalcio-serie-a/{season}/{matchday}`, verificato nel codice
pubblico del selettore del sito. Controlla anche stagione/giornata dichiarate nell'HTML e redazione di ogni tabella.
Non ripiega sull'ultima giornata se quella richiesta manca. Provenienza: **PAGINA_UFFICIALE**.

Il collector non cerca genericamente sul Web, non aggira login e non estrae cookie dal browser.
Per i feed JSON generici, il nome “Fantacalcio.it” nel contenuto non certifica la provenienza.

### Limiti della pagina pubblica

- Voti separati per Redazione Fantacalcio, Voto Statistico e Voto Italia; bonus della Redazione Fantacalcio, come indicato in tabella.
- Tutti i dati sono **PROVVISORI**: la pagina non attesta il consolidamento. Non è una garanzia di copertura live durante le partite.
- “Gol segnati” nella pagina esclude i rigori: il parser li somma per ottenere i gol totali, evitando doppi bonus.
- Codici speciali `55` e `56`: il CSS ufficiale rende rispettivamente un 6 segnaposto senza fantavoto e un trattino.
  Il collector li conserva senza voto/punteggio, senza inventare voti d'ufficio o confonderli con 5,5.
- Clean sheet non esplicito: resta sconosciuto (`null`), non falso. Se il regolamento assegna un bonus clean sheet,
  il fantavoto resta non calcolabile finché il dato non viene completato con un import appropriato.
- Player of the match, allenatori e modificatori non entrano nel motore di calcolo attuale.
- Nuove colonne, bonus mancanti, cartellini sconosciuti, login, redirect e metadati incompatibili bloccano l'import,
  lasciando intatti i dati precedenti. Il sito può cambiare struttura e richiedere manutenzione.

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
Clean sheet deve essere esplicito; `clean_sheet: null` indica informazione sconosciuta e impedisce il calcolo se il relativo bonus è attivo.
`vote: null` o `S.V.` produce un fantavoto nullo, mai zero.
Sostituzioni, voti d'ufficio, assist a pesi multipli e modificatori di squadra non sono automatici.
Duplicati/omonimi sono rifiutati. Stato assente = PROVVISORIO; DEFINITIVO deve essere dichiarato.
Un insieme misto assume lo stato meno consolidato.

## Aggiornamento automatico

I feed JSON/CSV devono dichiarare `provider, edition, season, matchday` (in CSV, colonne ripetute).
Per la pagina pubblica questi controlli sono svolti dal parser HTML dedicato.
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

Uscita 1 se il feed manca o fallisce. Errori/HTML generico/login non alterano i dati validi.
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

Test con fixture sintetiche e DB temporaneo. Il 2 settembre 2026 è stata verificata anche la pagina pubblica:
319 record per 2026-27/giornata 2, 320 per 2025-26/giornata 38; confronto di 580 fantavoti numerici
con il regolamento standard senza differenze. Questi conteggi sono evidenza di una verifica, non dati incorporati nel codice.
La prova autenticata sulla lega reale resta da fare.

Repository esaminati, senza riuso:
- [gingardia/fantacalcio-scraper](https://github.com/gingardia/fantacalcio-scraper): cerca soprattutto formazioni recuperate dal sistema; non connettore completo.
- [riccardopiola/fantacalcio-downloader](https://github.com/riccardopiola/fantacalcio-downloader): XLSX, dichiarato testato il 26 gennaio 2023.
- [piopy/fantacalcio-py](https://github.com/piopy/fantacalcio-py): pipeline scouting FPEDIA/FSTATS.
- [mastrogpt/fantacalcio-project](https://github.com/mastrogpt/fantacalcio-project): progetto distinto, non incluso.

La [pagina voti Fantacalcio.it](https://www.fantacalcio.it/voti-fantacalcio-serie-a) distingue Redazione Fantacalcio,
Voto Statistico e Voto Italia: la redazione fa quindi parte della chiave dei dati.
