# Crimson Desert Italian Voice Mod v0.3-hotfix-20260526

Pacchetto pronto per installare le voci italiane generate per Crimson Desert.

## Stato

- Voci italiane incluse: 51,246
- Package voce modificato: `0006`
- Payload audio: `data/wem_replacements_0006/`
- Python portatile: incluso in `installer/python`
- Data hotfix: `2026-05-26`
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

Su build diverse puo funzionare, ma non e garantito. Se il gioco viene aggiornato e aggiunge nuove quest o nuovi audio, quelli resteranno originali. Se una patch rinomina o rimuove audio presenti nel manifest, l'installer si ferma prima di patchare.

## Installazione

1. Chiudi Crimson Desert, Steam e CrimsonForge.
2. Avvia `CONTROLLA_PRIMA.cmd`.
3. Avvia `INSTALLA_MOD_VOCI_ITALIANE.cmd`.
4. Se richiesto, indica la cartella di installazione di Crimson Desert.

L'installer crea un backup automatico degli archivi modificati.

## Qualita realistica

Questa e una beta AI fan-made, non un doppiaggio professionale. Molte battute sono comprensibili e giocabili, ma alcune possono avere accento inglese/straniero, ritmo imperfetto, enfasi strana o pronunce da correggere manualmente.

Per eliminare davvero gli accenti servirebbe un secondo progetto piu lungo con voci italiane dedicate e revisione manuale.

## Nota non commerciale

Questo pacchetto e gratuito e non a scopo di lucro. Gli audio sono generati con AI e derivano/sono condizionati dalle voci originali del gioco: non venderlo, non metterlo dietro paywall e non monetizzarlo.
