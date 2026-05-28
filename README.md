Dona con PayPal per acquistare crediti AI e velocizzare il processo di traduzione.
<div style="text-align:center; margin-top:40px;">
  <a href="https://www.paypal.com/donate/?business=matteo.sai%40hotmail.it&currency_code=EUR"
     target="_blank"
     rel="noopener noreferrer">
    <img
      src="https://www.paypalobjects.com/en_US/i/btn/btn_donateCC_LG.gif"
      alt="Dona con PayPal"
      style="border:0;">
  </a>
</div>

# Crimson Desert Italian Voice Mod

Doppiaggio italiano AI fan-made per Crimson Desert.

## Anteprime audio v0.4

Sto preparando la versione `0.4` con una nuova passata ibrida ElevenLabs + modelli locali.

[Apri il player audio v0.4](https://cdn.jsdelivr.net/gh/teogsxr/Mod_Translate@main/community/voice-previews/v0.4-work-in-progress/player.html)

[Vai alla cartella delle anteprime v0.4](community/voice-previews/v0.4-work-in-progress#sample-per-personaggio)

Sono preview di sviluppo, non file da installare nel gioco. Servono per far sentire come stanno evolvendo le voci e raccogliere feedback su tono, accento, emozione e coerenza dei personaggi.

[Lascia un feedback audio strutturato](https://github.com/teogsxr/Mod_Translate/issues/new?template=voice-feedback.yml)

La mod attuale è una beta ampia e giocabile: copre il gioco con audio italiano generato tramite AI, ma non è ancora un doppiaggio professionale. Giorno per giorno sto correggendo le parti più visibili, soprattutto prologo, personaggi principali, antagonisti, mercanti e guardie.

## Stato attuale

- Release pubblica corrente: `0.3.1-compat-20260527`.
- Prossima release prevista: `0.4`, non ancora pubblicata.
- Voci italiane incluse nella release attuale: 51.246 file WEM.
- Package voce modificato: `0006`.
- Verifica locale più recente: 28/05/2026.
- Compatibilità verificata: Steam buildid `23374070`, `CrimsonDesert.exe` `1.0.0.1492`.
- Ultimo aggiornamento Steam rilevato: 24/05/2026 11:09 +02:00.
- Steam supportato.
- Epic, GOG e altri vendor: non ancora verificati; testate e segnalate se funziona.
- Xbox App / Microsoft Store: installazione bloccata per sicurezza finché non capiamo perché un utente ha avuto errore all'avvio.

La cartella installabile principale è:

`CrimsonDesert_ItalianVoiceMod_GITHUB_READY_v0.2_20260524`

Il nome cartella resta storico, ma il contenuto è aggiornato alla release indicata sopra.

## Sviluppo in corso

La versione attuale resta disponibile perché rende il gioco giocabile in italiano. In parallelo sto rifacendo le voci più importanti con una qualità più alta.

Per contenere i costi sto usando un mix di:

- modelli locali, più lenti ma senza consumo di crediti cloud;
- ElevenLabs e modelli cloud per le scene o le voci che devono venire molto meglio;
- correzioni manuali su pronunce, ritmo, volume, enfasi e sottotitoli.

Questo rallenta la parte fatta bene, ma permette di continuare senza bloccare il progetto. La priorità adesso è completare e rendere giocabile tutto il gioco; in una seconda fase migliorerò le voci una per una.

La voce di Kliff, per esempio, è ancora in revisione: alcune versioni funzionano, altre non mi convincono del tutto. Preferisco prima sistemare la copertura generale e poi tornare sulle voci principali con più calma.

Dettagli e sample sono in:

`community/`

## Aiutare con le voci

Se volete aiutare, potete creare o provare voci su ElevenLabs e mandarmi:

- personaggio a cui è destinata la voce;
- prompt usato per crearla;
- impostazioni principali, se le avete cambiate;
- file audio di preview o link;
- nota sul tono desiderato, per esempio protagonista avventuroso, anziano roco, antagonista profondo, soldato giovane, mercante, guardia.

Potete aprire una Issue GitHub usando il template [Feedback voce / audio](https://github.com/teogsxr/Mod_Translate/issues/new?template=voice-feedback.yml).

## Installazione da GitHub

1. Scarica la repository.
2. Apri `CrimsonDesert_ItalianVoiceMod_GITHUB_READY_v0.2_20260524`.
3. Avvia `CONTROLLA_PRIMA.cmd`.
4. Avvia `INSTALLA_MOD_VOCI_ITALIANE.cmd`.

Il pacchetto GitHub include Python portatile in `installer/python`, quindi non richiede Python installato nel sistema.

## Utenti Xbox App / Microsoft Store

Al momento Xbox App/Microsoft Store è bloccata per sicurezza. Non significa che sarà impossibile supportarla: serve capire se gli archivi Microsoft sono diversi da quelli Steam o se Xbox blocca i file modificati con un controllo integrità esterno.

Per aiutarmi:

1. Apri `tools/xbox-compatibility-diagnostic/`.
2. Avvia `DIAGNOSTICA_XBOX_COMPATIBILITA.cmd`.
3. Carica il file `.txt` o `.json` generato nella cartella `reports`.
4. Apri una Issue usando il template `Compatibilità Xbox App`.

Il tool standalone non installa la mod e non modifica il gioco.

Link diretto al tool: https://github.com/teogsxr/Mod_Translate/tree/main/tools/xbox-compatibility-diagnostic

## Qualità e limiti

Questa è una beta AI ampia, giocabile, ma non un doppiaggio professionale. Alcune frasi possono avere accento inglese o straniero, ritmo imperfetto, enfasi strana, volume non perfetto, frasi troppo veloci o pronunce da rifinire.

Le prime generazioni sono state fatte in massa per avere una base completa. Le correzioni nuove sono più curate, ma richiedono tempo e crediti AI.

Feedback e correzioni puntuali sono benvenuti, soprattutto con nome personaggio, scena, frase pronunciata e problema sentito.

## Nexus Mods

Pagina Nexus: https://www.nexusmods.com/crimsondesert/mods/2741

La variante Nexus è più prudente: non include Python portatile e richiede Python 3 installato sul PC. Questo riduce falsi positivi e problemi di scansione del portale.

## Uso non commerciale

Mod fan gratuita e non a scopo di lucro. Gli audio sono generati con AI e derivano o sono condizionati dalle voci originali del gioco. Non vendere il pacchetto, non metterlo dietro paywall e non monetizzarlo.
