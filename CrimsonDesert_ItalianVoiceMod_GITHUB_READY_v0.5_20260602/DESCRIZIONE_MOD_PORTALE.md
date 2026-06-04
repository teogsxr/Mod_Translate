# Crimson Desert Italian Voice Mod 0.5-beta-20260604 Beta

Doppiaggio italiano AI fan-made per Crimson Desert.

## In breve

Questa mod applica 51.461 file audio italiani al package voce `0006`. Il pacchetto GitHub contiene i WEM sostitutivi, un manifest e un installer che applica le modifiche alla copia installata del gioco.

## Compatibilita verificata

- Steam AppID: `3321460`
- Steam buildid testato: `23374070`
- `CrimsonDesert.exe`: `1.0.0.1492`
- Release pacchetto: `2026-06-04`

Steam e la piattaforma verificata.

Epic/GOG o installazioni manuali possono funzionare solo se gli archivi sono compatibili; l'installer chiede una conferma esplicita prima di procedere.

Xbox App/Microsoft Store non e attualmente supportata. Un utente ha segnalato errore all'avvio dopo patch; per sicurezza l'installer blocca Xbox App e include `DIAGNOSTICA_COMPATIBILITA.cmd` per generare un report da inviare.

Su GitHub e disponibile anche un tool standalone per utenti Xbox:

`tools/xbox-compatibility-diagnostic/`

Su versioni diverse potrebbe funzionare, ma non e garantito. Se il gioco viene aggiornato e aggiunge nuovi audio, quei nuovi audio resteranno originali.

## Qualita delle voci

Release beta molto ampia ma non perfetta. Le voci sono state generate clonando o condizionando le voci originali, quindi in alcune frasi si puo sentire accento inglese o straniero, una cadenza non del tutto italiana, enfasi poco naturale o pronunce da rifinire.

La mod e pensata per rendere il gioco giocabile in italiano, non per sostituire un doppiaggio professionale. Feedback e correzioni puntuali sono benvenuti.

## Installazione

1. Chiudi il gioco.
2. Avvia `CONTROLLA_PRIMA.cmd`.
3. Avvia `INSTALLA_MOD_VOCI_ITALIANE.cmd`.
4. Se richiesto, indica la cartella di installazione di Crimson Desert.

L'installer crea un backup automatico degli archivi modificati.

Nota upgrade da `0.3` / `0.3.1`: prima di installare la `0.5` e' consigliato ripristinare o cancellare gli archivi `0006` gia patchati, poi farli riscaricare/verificare da Steam. La `0.5` lascia volutamente alcune urla e battute brevi nella voce originale inglese perche risultano piu naturali: partire da una base pulita evita che restino vecchie voci AI della `0.3` in quei punti.

## Uso e distribuzione

Progetto fan gratuito, non commerciale. Gli audio sono generati con AI e derivano o sono condizionati dalle voci originali del gioco; per questo e vietata la vendita, il paywall o qualunque monetizzazione del pacchetto.

## Nota v0.5 beta

Questa build include lo stato live aggiornato del package voce `0006` al 04/06/2026. Comprende il delta della v0.4, i rollback selettivi alle voci piu stabili, i fix `0.5` mantenuti, il fix dei nomi comuni fuori intro e 223 file audio aggiuntivi committati nel gioco con verifica SHA. Alcune righe restano volutamente escluse se il testo o il candidato audio non sono ancora sicuri.


## Changelog rapido v0.5 beta

- Payload voce estratto dal gioco live aggiornato al 04/06/2026.
- 223 file audio aggiuntivi committati nel gioco con WEM Vorbis e verifica SHA.
- Include anche il delta tecnico gia presente nel gioco dopo le modifiche del 01/06/2026, i rollback selettivi e il fix dei nomi comuni fuori intro del 04/06/2026.
- 35 righe hanno testo audio adattato e sono tracciate per riallineamento sottotitoli/Forge.
- La pipeline 0.5 usa gating qualit?: pronuncia, voce, emozione, volume e finali vocalici contano piu del timing perfetto al millisecondo.
