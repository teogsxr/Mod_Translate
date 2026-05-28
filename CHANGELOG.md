# Changelog

## Work in progress - 0.4 voice pass

- In preparazione una release `0.4` con correzioni qualitative sulle voci, senza cambiare oggi i file pubblici della mod.
- Il lavoro sta usando un mix di modelli locali e cloud per ridurre i costi AI: la qualità alta richiede più tempo, ma permette di tenere il gioco giocabile e completo mentre le voci vengono migliorate.
- Priorità attuale: prologo, main quest iniziale, Kliff, Myurdin, Sebastian, personaggi del prologo, mercanti e guardie.
- Alcune voci principali, inclusa Kliff, restano in revisione: prima verrà migliorata la copertura generale, poi verranno rifinite le voicebank una per una.
- Aggiunte note per chi vuole contribuire con prompt e sample ElevenLabs.
- Ribadita compatibilità verificata su Steam buildid `23374070`, `CrimsonDesert.exe` `1.0.0.1492`; Xbox App/Microsoft Store resta bloccata finché non arrivano report diagnostici.

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
