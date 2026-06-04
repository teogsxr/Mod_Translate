# Community e sviluppo

Questa cartella non contiene file necessari per installare la mod.

Serve per mostrare in modo trasparente cosa sta cambiando nella prossima versione, raccogliere pareri sulle voci e tenere separati i test audio dalla patch vera e propria.

## Stato del lavoro

La versione installabile attuale resta nella cartella:

`CrimsonDesert_ItalianVoiceMod_GITHUB_READY_v0.5_20260602`

La `0.5` e' lo stato giocabile corrente: copre moltissimo audio in italiano, recupera le versioni precedenti dove suonavano meglio e mantiene i fix piu recenti solo nei punti in cui migliorano davvero la resa.

Per contenere i costi AI sto usando un mix di modelli locali e servizi cloud. Le parti piu importanti vengono curate meglio, ma richiedono piu tempo e crediti. Al momento il progetto ha un pubblico piccolo, quindi la rifinitura premium andra avanti in modo mirato, soprattutto dove arrivano feedback concreti.

- Kliff, Myurdin, Oongka e alcuni personaggi dell'intro sono stati stabilizzati usando le versioni che funzionano meglio nello stato attuale.
- Alustin/eremita e Strega Bianca hanno una voce 0.5 approvata.
- Alcuni casi di nomi comuni, come Ibano/Aveeno ed erboristi, sono stati corretti dove esistevano sorgenti voce sicure.
- Molti NPC secondari, venditori e guardie restano una copertura italiana provvisoria: sono utili per giocare/ascoltare il gioco in italiano, ma non sono ancora doppiaggio finale personaggio per personaggio.

## Come lasciare feedback

Apri una Issue su GitHub e indica:

- scena o punto del gioco;
- personaggio;
- cosa non va, per esempio accento, voce sbagliata, ritmo, audio muto, frase troncata;
- se possibile il nome file WEM o uno screenshot della riga in CrimsonForge.

Per i sample audio, guarda `voice-previews/`.

Per i prompt e lo storico delle voci, guarda `voice-templates/`.

## Contribuire con voci ElevenLabs

Se vuoi proporre una voce, apri una Issue e allega:

- personaggio;
- prompt usato;
- impostazioni principali, se modificate;
- preview audio o link;
- idea del tono, per esempio protagonista roco, anziano, antagonista profondo, soldato giovane, mercante o guardia.

Non serve rifare tutto il gioco: anche un buon prompt per un personaggio importante puo aiutare molto.

Prima di proporre una voce nuova, controlla anche `voice-templates/`: contiene i prompt già usati, gli script di preview e le note sui personaggi.
