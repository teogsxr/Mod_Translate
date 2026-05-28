# Crimson Desert Italian Voice Mod 0.4-beta-20260528

Pacchetto pronto per installare le voci italiane AI fan-made di Crimson Desert.

## Stato

- Voci italiane incluse: 51.461
- Package voce modificato: `0006`
- Payload audio: `data/wem_replacements_0006/`
- Python portatile: incluso in `installer/python`
- Data release: `2026-05-28`

## Compatibilita verificata

- Steam AppID: `3321460`
- Steam buildid testato: `23374070`
- `CrimsonDesert.exe`: `1.0.0.1492`

Steam e la piattaforma verificata.

Epic/GOG o installazioni manuali possono funzionare solo se gli archivi del gioco sono compatibili; l'installer chiedera una conferma esplicita prima di procedere.

Xbox App/Microsoft Store non e attualmente supportata: un utente ha segnalato errore all'avvio del gioco dopo patch. Per questa versione l'installer blocca Xbox App per sicurezza. Se hai la versione Xbox, avvia `DIAGNOSTICA_COMPATIBILITA.cmd` e invia il report su GitHub/Nexus.

Se non vuoi scaricare tutto il pacchetto solo per il report, usa il tool standalone su GitHub:

`tools/xbox-compatibility-diagnostic/`

Su build diverse puo funzionare, ma non e garantito. Se il gioco viene aggiornato e aggiunge nuove quest o nuovi audio, quelli resteranno originali. Se una patch rinomina o rimuove audio presenti nel manifest, l'installer si ferma prima di patchare.

## Installazione

1. Chiudi Crimson Desert, il launcher dello store e CrimsonForge.
2. Avvia `CONTROLLA_PRIMA.cmd`.
3. Avvia `INSTALLA_MOD_VOCI_ITALIANE.cmd`.
4. Se richiesto, indica la cartella di installazione di Crimson Desert.

L'installer crea un backup automatico degli archivi modificati prima di applicare i WEM italiani.

### Upgrade da 0.3 / 0.3.1

Prima di installare la `0.4` sopra una `0.3` o `0.3.1`, e' consigliato ripristinare o cancellare gli archivi `0006` gia patchati e poi farli riscaricare/verificare da Steam.

La `0.4` lascia volutamente alcune urla e battute brevi nella voce originale inglese perche risultano piu naturali rispetto alla vecchia generazione AI. Partire da una base pulita evita che in quei punti restino file audio della `0.3`.

## Diagnostica per store non compatibili

Se il gioco e installato con Xbox App/Microsoft Store, o se l'installer non trova il gioco, avvia:

`DIAGNOSTICA_COMPATIBILITA.cmd`

Il comando crea un report JSON in `compatibility_reports/`. Allegalo a una Issue o a un commento Nexus: non contiene chiavi API o dati personali, solo percorso, store rilevato, versione exe, dimensioni e SHA256 degli archivi necessari.

## Cosa cambia

- Aggiunge voci italiane al package audio `0006`.
- Mantiene le strutture originali del gioco e sostituisce solo i WEM indicati dal manifest.
- Include hotfix per voci mancanti, righe con placeholder `StaticInfo` lette male e alcune battute iniziali corrette manualmente.

## Qualita realistica

Questa e una beta AI fan-made, non un doppiaggio professionale. Molte battute sono comprensibili e giocabili, ma alcune possono avere accento inglese o straniero, ritmo imperfetto, enfasi strana o pronunce da correggere manualmente.

Per eliminare davvero gli accenti servirebbe un progetto piu lungo con voci italiane dedicate, scelta dei riferimenti voce personaggio per personaggio e revisione manuale.

## Nota non commerciale

Questo pacchetto e gratuito e non a scopo di lucro. Gli audio sono generati con AI e derivano o sono condizionati dalle voci originali del gioco: non venderlo, non metterlo dietro paywall e non monetizzarlo.
