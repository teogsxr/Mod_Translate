# Changelog

## 0.5-beta cleanup - 20260604

- Documentato lo stato corrente della `0.5` come build giocabile ripulita, non come la precedente passata sperimentale/sporca.
- Aggiornata la pagina iniziale con distinzione tra:
  - voci stabilizzate o approvate;
  - copertura italiana provvisoria utile per giocare/ascoltare il gioco in italiano;
  - personaggi e NPC ancora in revisione.
- Registrato il rollback selettivo dello stato operativo:
  - Kliff: base 0.4, con urli/comandi 0.5 dove funzionano meglio;
  - Myurdin: base 0.4;
  - Oongka: base 0.4 con urli/comandi 0.5;
  - Carl e soldati intro: base 0.4;
  - Alustin/eremita e Strega Bianca: voci 0.5 approvate.
- Documentato il fix parziale dei nomi comuni fuori intro: 59 WEM patchati live e verificati SHA usando solo sorgenti voce sicure.
- Chiarito che la rifinitura premium delle voci andra avanti in modo mirato, in base a feedback concreti e interesse della community, perche il costo AI del progetto e' significativo.

## 0.5-beta-20260602

- Preparato pacchetto GitHub `CrimsonDesert_ItalianVoiceMod_GITHUB_READY_v0.5_20260602`.
- Preparato pacchetto Nexus-safe `CrimsonDesert_ItalianVoiceMod_NEXUS_SAFE_v0.5_20260602` senza caricarlo.
- Payload v0.5: 51.461 WEM italiani.
- Delta reale rispetto alla v0.4: 700 WEM cambiati, 0 aggiunti, 0 rimossi.
- Commit tecnico del 02/06/2026: 223 WEM patchati nel gioco, convertiti in WEM Vorbis, repackati e verificati SHA.
- Il delta 0.5 include anche modifiche gia integrate nel lavoro del 01/06/2026, compresi volantini, avvisi/testi letti e altri fix tecnici.
- Aggiunto confronto release:
  - backup originale -> v0.4: 51.461 WEM diversi;
  - v0.4 -> v0.5: 700 WEM cambiati;
  - backup originale -> v0.5: 51.461 WEM diversi.
- Aggiornato il metodo operativo: la pipeline ora usa quality gate su voce, pronuncia, emozione, volume, finali vocalici, effetto radio e coerenza testo prima di committare audio nel gioco.
- Tracciate 35 righe con `subtitle_update_required` per riallineamento futuro tramite mapping Forge sicuro.

## Work in progress - 0.6

- Prossima fase: produzione personaggio per personaggio con template voce/emozione corretti.
- I feedback utenti verranno raccolti come coda operativa, trasformati in piano tecnico e applicati solo dopo approvazione dell'autore della mod.
- Le righe escluse dalla 0.5 per testo sospetto o candidato non verificabile restano in backlog, non vengono scartate.

## 0.4-beta-20260528

- Pubblicato pacchetto `0.4` con 51.461 WEM italiani.
- Aggiunte 220 righe prioritarie recuperate dall'audio originale inglese e rigenerate in italiano.
- Esclusi volutamente 5 urli/battute brevi di Kliff dal payload: restano originali inglesi perche suonano piu naturali della vecchia generazione AI.
- Il recupero massivo delle righe senza testo e' pronto come manifest/lavoro tecnico, ma viene spostato alla `0.5` per non pubblicare una passata troppo automatica senza review.
- Per l'upgrade da `0.3` / `0.3.1` e' consigliato ripristinare/cancellare gli archivi `0006` gia patchati prima di installare la `0.4`, cosi i file lasciati originali non restano presi dalla vecchia voce AI.
- Ribadita compatibilita verificata su Steam buildid `23374070`, `CrimsonDesert.exe` `1.0.0.1492`; Xbox App/Microsoft Store resta bloccata finche non arrivano report diagnostici.

## 0.3.1-compat-20260527

- Aggiunto autodetect percorsi per Steam, Epic, GOG, XboxGames e percorsi manuali.
- Aggiunto `DIAGNOSTICA_COMPATIBILITA.cmd` per creare report senza modificare il gioco.
- Aggiunto dry-run compatibilita prima della patch.
- Bloccata per sicurezza l'installazione su Xbox App/Microsoft Store: e' stato segnalato errore all'avvio dopo patch e serve verificare gli archivi prima di dichiararla compatibile.
- Aggiornate istruzioni GitHub/Nexus con matrice compatibilita store.

## Work in progress - revisione voci

- Aggiunta cartella `community/` per spiegare il lavoro in corso senza toccare la release installabile.
- Aggiunti sample audio separati dalla patch: candidati voce Kliff e sample antagonista Myurdin.
- Aggiunta roadmap per la futura revisione qualitativa delle voci.
- Aggiunto template GitHub Issue per feedback su voci, pronunce, accenti, audio muti o frasi troncate.

## 0.3-hotfix-20260526

- Riallineato il pacchetto GitHub con 51.246 WEM italiani completati.
- Aggiunti hotfix per voci mancanti e blocchi non tradotti della main quest.
- Corretti casi in cui il TTS leggeva placeholder tipo `StaticInfo` invece del nome reale.
- Integrate correzioni manuali sull'intro e sulle prime battute segnalate durante il test.
- Aggiornata la documentazione con limiti realistici sulla qualita AI e sulla natura non commerciale del progetto.

## 0.2-20260524

- Prima pubblicazione ampia del pacchetto voce italiana.
- Aggiunti installer, manifest audio e sorgenti di lavoro.
