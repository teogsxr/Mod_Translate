# Diagnostica Xbox App / Microsoft Store

Questa cartella contiene un tool standalone per gli utenti Xbox App / Microsoft Store.

Il tool non installa la mod e non modifica il gioco. Legge solo alcuni metadati e hash degli archivi necessari per capire se la versione Xbox puo essere supportata in sicurezza.

## Quando usarlo

Usalo se:

- hai Crimson Desert installato da Xbox App / Microsoft Store;
- la mod non parte o il gioco da errore all'avvio dopo una patch;
- vuoi aiutarci a capire se la versione Xbox usa archivi diversi dalla Steam.

## Come usarlo

1. Scarica questa cartella o l'intera repository.
2. Avvia `DIAGNOSTICA_XBOX_COMPATIBILITA.cmd`.
3. Se richiesto, indica la cartella di installazione di Crimson Desert.
4. Apri la cartella `reports`.
5. Carica il file `.txt` o `.json` generato in una Issue GitHub o in un commento Nexus.

## Cosa contiene il report

- store rilevato;
- percorso del gioco;
- versione e SHA256 di `CrimsonDesert.exe`;
- dimensione e SHA256 di `meta/0.papgt`;
- dimensione e SHA256 di `0006/0.pamt`, `0006/0.paz`, `0006/1.paz`;
- elenco cartelle package con `0.pamt`.

## Perche serve

La mod e stata verificata su Steam. Su Xbox App/Microsoft Store un utente ha segnalato errore all'avvio dopo patch. Prima di abilitare Xbox dobbiamo capire se:

- gli archivi Xbox sono diversi da quelli Steam;
- Xbox App blocca l'avvio per un controllo integrita esterno;
- serve un pacchetto patch dedicato alla versione Microsoft.

Fino a verifica completata, l'installer principale blocca Xbox App per evitare di rompere l'avvio del gioco.
