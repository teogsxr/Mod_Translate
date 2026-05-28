# Changelog

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
- Bloccata per sicurezza l'installazione su Xbox App/Microsoft Store: e stato segnalato errore all'avvio dopo patch e serve verificare gli archivi prima di dichiararla compatibile.
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
