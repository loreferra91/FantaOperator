# FantaOperator

FantaOperator è un workspace Streamlit per gestire una lega Classic: rosa, asta, scambi,
formazioni con panchina, calcolo della giornata e voti ufficiali. Il motore è locale e
deterministico; non richiede chiavi AI.

## Prima configurazione

1. In **Impostazioni** inserisci nome della lega, budget, stagione, giornata, panchina,
   limite sostituzioni, bonus/malus ed eventuale modificatore difesa.
2. In **Voti & dati** collega la fonte Gazzetta e sincronizza la giornata.
3. In **Rosa** importa il CSV della tua squadra o aggiungi i calciatori dai voti scaricati.
4. In **Formazione** salva titolari e panchina nell'ordine di ingresso.
5. Usa **Asta live** e **Mercato** per registrare acquisti, cessioni e scambi.

Le nuove installazioni partono senza calciatori di esempio. I database creati da versioni
precedenti conservano i dati dell'utente; la vecchia rosa dimostrativa viene rimossa soltanto
quando è ancora identica al campione originale e non esistono formazioni o movimenti.

## Voti ufficiali Gazzetta

La fonte predefinita è la [pagina voti de La Gazzetta dello Sport](https://www.gazzetta.it/calcio/fantanews/voti/serie-a-2026-27/).
Per ogni giornata il parser verifica titolo, stagione, giornata, colonne, squadra e identità
del calciatore prima di salvare i dati. Conserva separatamente:

- voto ufficiale Gazzetta;
- fantavoto pubblicato da Gazzetta;
- fantavoto ricalcolato con i bonus e malus della lega.

V, G, A, R, RS, AG, AM ed ES vengono acquisiti dalla pagina. Un S.V. resta privo di
punteggio. Il clean sheet non viene dedotto dal fantavoto pubblicato. I dati della pagina
sono indicati come **PROVVISORI** perché la pagina non attesta il consolidamento.

La sezione **Rosa** mostra media voto, fantamedia, gol, assist e trend calcolati su tutte le
giornate importate. Questi valori osservati restano separati dalle stime manuali di rendimento,
titolarità, rischio, valore e tier usate dall'ottimizzatore.

Sono supportati anche import XLSX, CSV e JSON con controllo di provider, redazione, stagione
e giornata. La vecchia fonte pubblica Fantacalcio.it rimane disponibile configurandola
esplicitamente.

## Formazioni e punteggio

Il motore valuta tutti i moduli Classic ammessi, salva uno snapshot dei titolari e della
panchina e applica sostituzioni in ordine, ruolo per ruolo, fino al limite configurato.
Non inventa un voto per chi rimane senza punteggio.

Il modificatore difesa è facoltativo e completamente configurabile. Con almeno quattro
difensori usa il voto base del portiere e dei tre migliori difensori entrati a voto, poi
applica la fascia più alta raggiunta. Le soglie e i bonus iniziali sono 6/+1, 6,5/+3 e 7/+6,
ma il modificatore è disattivato finché l'utente non lo abilita.

## Asta, mercato e backup

La War Room usa i calciatori ufficiali non ancora posseduti, il budget residuo reale e una
valutazione inserita dall'utente. Un acquisto confermato entra subito nella rosa. Cessioni e
scambi aggiornano rosa e registro movimenti in una singola transazione SQLite; un errore di
budget non lascia modifiche parziali.

Il backup JSON versione 2 include configurazione, rosa, formazioni e panchine, registro
movimenti e chat. Non include i voti, che possono essere risincronizzati, né URL privati.
I backup versione 1 restano importabili. Il ripristino valida l'intero file prima di mutare il DB.

Su Streamlit Community Cloud, in assenza di `FANTAOPERATOR_DB`, ogni browser usa un database
temporaneo isolato. Scarica il backup prima di chiudere o ricaricare la sessione. L'istanza
locale usa invece un file SQLite durevole.

## Avvio locale

Su macOS fai doppio clic su **Avvia FantaOperator.command**, oppure esegui:

```bash
python3 -m pip install -r requirements.txt
FANTAOPERATOR_DB="$PWD/data/fantaoperator.db" python3 -m streamlit run app.py --server.address localhost
```

L'avviatore riusa l'istanza già attiva, sceglie una porta libera tra 8501 e 8520 e non termina
altri processi. Il worker opzionale aggiorna la giornata configurata anche senza browser:

```bash
python3 -m fantaoperator.updater --watch
```

## Confini esterni

FantaOperator non invia operazioni alla piattaforma della lega. L'import automatico di rose,
formazioni, risultati e avversari da un'area privata richiede una sessione autorizzata e un
campione reale del formato restituito dal provider. `league_scraper.py` contiene i controlli
di rete e sessione, ma l'adattatore dello schema privato resta intenzionalmente bloccato finché
quel formato non viene verificato.

La modalità Mantra non viene calcolata con regole Classic. Previsioni live di convocazioni,
infortuni e probabili formazioni richiedono fonti separate e non vengono presentate come dati
Gazzetta ufficiali.

## Verifica

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q app.py fantaoperator
```

La suite copre parsing e provenienza, rettifiche, isolamento per stagione/redazione/giornata,
S.V., regole di punteggio, sostituzioni, modificatore difesa, operazioni atomiche di mercato,
backup, migrazioni, sicurezza degli URL e navigazione Streamlit.
