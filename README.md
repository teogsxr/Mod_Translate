# Crimson Desert Italian Voice Mod

Mod fan-made gratuita che aggiunge un doppiaggio italiano AI a Crimson Desert.

La cartella da scaricare/usare e:

`CrimsonDesert_ItalianVoiceMod_GITHUB_READY_v0.2_20260524`

Il nome della cartella resta quello della prima pubblicazione per non rompere link e riferimenti, ma il contenuto e stato aggiornato alla versione `0.3-hotfix-20260526`.

## Stato attuale

- Versione pacchetto: `0.3-hotfix-20260526`
- Data hotfix: `2026-05-26`
- Voci italiane incluse: `51,246`
- Package voce modificato: `0006`
- Compatibilita verificata: Steam buildid `23374070`, `CrimsonDesert.exe` `1.0.0.1492`
- Stato verifica tecnica: tutti i target voce non testuali risultano patchati; hard error WEM rilevati e corretti.

## Cosa cambia nella hotfix

- Aumentata la copertura audio rispetto alla prima build pubblicata: il pacchetto passa da circa `42,759` WEM a `51,246` WEM.
- Recuperate molte voci mancanti di main quest, cutscene, scene dialogue e dialoghi ambientali.
- Corrette alcune voci iniziali del prologo, inclusa la pronuncia di `Giails` e alcune battute di Kliff.
- Riparati WEM corrotti o non validi trovati durante l'audit.
- Aggiornati manifest, progressi, sorgenti e report di controllo.
- Aggiunto Python portatile nel pacchetto: l'utente non deve installare Python manualmente.

## Cosa contiene

- Installer pronto all'uso per applicare le voci italiane al package voce `0006`.
- Payload audio gia esploso in `data\wem_replacements_0006\`, senza zip audio annidato.
- Python portatile incluso in `installer\python\`.
- Manifest, report, script e sorgenti in `sources\` per chi vuole controllare o continuare il lavoro.

## Installazione rapida

1. Scarica il repository da GitHub.
2. Entra in `CrimsonDesert_ItalianVoiceMod_GITHUB_READY_v0.2_20260524`.
3. Avvia `CONTROLLA_PRIMA.cmd`.
4. Avvia `INSTALLA_MOD_VOCI_ITALIANE.cmd`.

L'installer crea un backup automatico degli archivi modificati. Se la versione del gioco non corrisponde o mancano file attesi, si ferma prima di patchare.

## Qualita e limiti

Questa e una beta AI fan-made, non un doppiaggio professionale. Molte frasi sono giocabili e comprensibili, ma alcune possono avere accento inglese/straniero, ritmo non naturale, enfasi strana o pronunce da sistemare manualmente.

La mod e gratuita e non commerciale. Le voci sono generate con AI e derivano/sono condizionate dalle voci originali del gioco: non vendere, non mettere dietro paywall e non monetizzare il pacchetto.
