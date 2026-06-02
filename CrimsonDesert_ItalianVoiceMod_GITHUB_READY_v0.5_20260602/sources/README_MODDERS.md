# Crimson Desert Italian Voice Mod - sorgenti v0.2

Questo pacchetto serve a verificare, modificare o continuare la mod.

## Cosa contiene

- `crimsonforge_modified_source/`: sorgente CrimsonForge usato per generare e patchare gli audio.
- `manifests/`: manifest dei WEM inclusi nel pacchetto ready, piu stato progress/target usato durante la generazione.
- `scripts/`: installer, script stato e script di packaging.
- `ready_package_helpers/`: documenti e helper del pacchetto pronto, senza payload audio completo.

## Stato release

- Package group: `0006`
- Voci italiane incluse: 42,759
- Text Dialogue esclusi: 243
- Steam buildid testato: `23374070`
- `CrimsonDesert.exe`: `1.0.0.1492`

## Modificare una singola voce

1. Avvia CrimsonForge patchato.
2. Apri il game path di Crimson Desert.
3. Cerca il WEM dal manifest.
4. Rigenera una riga alla volta, preferibilmente con controllo ascolto.
5. Usa `Generate + Patch` sulla riga singola.
6. Ricrea il pacchetto ready con lo script di packaging.

## Ricreare il pacchetto ready

Lo script principale e:

```powershell
python scripts\make_crimson_mod_packages_v02.py
```

I percorsi sono quelli di questo PC e vanno aggiornati se lavori su un'altra macchina.

## Note tecniche importanti

- Il batch di CrimsonForge patchato salta i record gia completati, cosi non rigenera tutto per errore.
- Le righe `Text Dialogue` sono state escluse dal pacchetto ready.
- Il sorgente non include archivi `.paz` originali del gioco.
- Il ready package include solo WEM sostitutivi e script di patch.

## Qualita e diritti

Questa e una beta AI non commerciale. Le voci sono clonate/condizionate dalle voci originali, quindi non va venduta o monetizzata.
Per migliorare l'accento servono nuove voci italiane dedicate e revisione manuale.
