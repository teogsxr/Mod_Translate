# Voice templates

Questa cartella conserva i template delle voci usate o provate per la mod italiana di Crimson Desert.

Scopo pratico: se una voce viene cancellata da ElevenLabs, o se dobbiamo ricrearla con un altro account/modello, qui restano prompt, script di preview, note di scelta e storico dei voice id.

## Struttura

- _index.csv e _index.json: indice rapido di tutti i profili.
- Una cartella per profilo/personaggio, per esempio unique-kliff/ o unique-myurdin/.
- Dentro ogni cartella:
  - oice_design_prompt.txt: prompt principale da copiare in ElevenLabs Voice Design.
  - preview_script_it.txt: frase/testo usato per sentire se la voce funziona.
  - oice-template.json: storico completo di tentativi, preview e voice id.
  - README.md: riassunto leggibile.

## Regole importanti

- Non salvare mai API key, token, cookie o credenziali.
- I voice id salvati qui sono solo storico tecnico: non sono credenziali e non sostituiscono il prompt.
- Voice bank non significa sempre personaggio. Guardie, mercanti e cittadini vanno assegnati con regole stabili per contesto, non solo per nome file.
- Per le battute emotive usare tag nel 	ts_text, non nei sottotitoli puliti.
- Per il TTS locale evitare punti finali nel testo parlato quando il motore tende a leggere punto.

## Uso consigliato

1. Apri la cartella del personaggio.
2. Copia oice_design_prompt.txt in ElevenLabs Voice Design.
3. Usa preview_script_it.txt per generare 2-3 preview.
4. Aggiorna oice-template.json o il README con la variante scelta.
5. Se la voce diventa canonica, annota nome voce, voice id e data.

Ultimo export: 2026-05-28.