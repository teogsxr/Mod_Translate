# Crimson Desert Italian Voice Mod v0.3-hotfix-20260526

Pacchetto pronto per installare solo le voci italiane generate per Crimson Desert.
Non serve installare Python: il runtime ufficiale portatile e incluso nel pacchetto.

## Stato

- Audio italiani patchabili: 51,246 WEM
- Package voce modificato: `0006`
- Payload audio: `data/wem_replacements_0006/`
- File `.paz` originali inclusi: nessuno
- Backup automatico prima della scrittura
- Verifica tecnica: tutti i target voce non testuali risultano coperti; hard error WEM rilevati e corretti.

## Cosa cambia rispetto alla prima pubblicazione

- Copertura ampliata da circa `42,759` a `51,246` WEM.
- Recuperate molte voci mancanti di main quest, cutscene, scene dialogue e dialoghi ambientali.
- Corrette alcune battute del prologo, inclusa la pronuncia di `Giails` e alcune frasi di Kliff.
- Riparati WEM corrotti o non validi trovati durante l'audit.
- Aggiornati manifest, report di controllo e sorgenti inclusi nel pacchetto.
- Incluso Python portatile per evitare prerequisiti manuali lato utente.

## Compatibilita verificata

- Steam AppID: `3321460`
- Steam buildid testato: `23374070`
- `CrimsonDesert.exe`: `1.0.0.1492`
- Data hotfix: `2026-05-26`

Questa e la versione supportata con certezza. Su altre build puo funzionare, ma non e garantito.
Se il gioco viene aggiornato e aggiunge nuovi audio, quei nuovi audio restano originali/inglesi.
Se una patch rinomina o rimuove audio gia presenti nel manifest, l'installer si ferma prima di modificare gli archivi.

## Installazione rapida

1. Chiudi Crimson Desert, Steam Cloud sync in corso e CrimsonForge.
2. Estrai o scarica tutta la cartella della mod in una posizione qualsiasi.
3. Avvia `CONTROLLA_PRIMA.cmd`.
4. Avvia `INSTALLA_MOD_VOCI_ITALIANE.cmd`.
5. Se il gioco non e nel percorso Steam standard, inserisci la cartella di Crimson Desert quando richiesto.
6. Avvia il gioco e usa la lingua voce inglese/il package voce `0006`.

L'installer modifica:

- `0006\0.pamt`
- `0006\0.paz`
- `0006\1.paz`
- `meta\0.papgt`

Il backup viene creato in:

`Crimson Desert\crimson_desert_it_voice_backup\DATA_ORA`

## Disinstallazione

Metodo consigliato: da Steam usa "Verifica integrita dei file installati".

Metodo manuale: copia dal backup i file `meta\0.papgt`, `0006\0.pamt`, `0006\0.paz` e `0006\1.paz` nella cartella del gioco.

## Qualita realistica

Questa e una beta AI fan-made, non un doppiaggio professionale.
Le voci sono state generate clonando/condizionando le voci originali: molte battute sono giocabili e comprensibili, ma alcune possono avere accento inglese o straniero, ritmo imperfetto, enfasi strana, pause non ideali o resa emotiva non sempre naturale.

Per eliminare davvero gli accenti servirebbe un secondo progetto piu lungo con voci italiane dedicate, profili separati per personaggio e revisione manuale.

## Nota non commerciale

Questo pacchetto e un progetto fan gratuito e non a scopo di lucro.
Gli audio sono generati con AI e derivano/sono condizionati dalle voci originali del gioco: non venderlo, non metterlo dietro paywall e non monetizzarlo.
Rispetta le regole del gioco, della piattaforma e dei titolari dei diritti. Se un avente diritto chiede la rimozione, il pacchetto va rimosso.
