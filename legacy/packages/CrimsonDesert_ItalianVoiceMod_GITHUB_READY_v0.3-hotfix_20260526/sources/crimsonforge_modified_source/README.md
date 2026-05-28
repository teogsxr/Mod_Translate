# CrimsonForge

CrimsonForge is a complete modding studio for **Crimson Desert**.

It lets you browse game archives, translate localization with AI, inspect and replace meshes, replace audio, generate dialogue voices, patch fonts, and write everything back with valid checksums so the game still launches normally.

CrimsonForge handles the full pipeline:

`decrypt (ChaCha20) -> decompress (LZ4) -> parse -> modify -> recompress -> re-encrypt -> update PAMT -> update PAPGT -> ready to play`

## Current Release

**Latest version:** `1.11.0`

Recent highlights:

- Full PAC round-trip editing now supports export, edit, add/delete geometry, re-import, and patch back to game for topology-changing meshes
- Explorer can search by live in-game item names like `Vow of the Dead King`
- Search history now persists across Explorer, Audio, and Translate
- Explorer 3D preview uses a much faster hardware-accelerated OpenGL viewer
- Bundled standalone builds now resolve runtime data correctly and include the full `data` directory

## Main Features

### Archive Explorer

- Browse more than **1.4 million** files across the game packages with fast filtering
- Preview textures, meshes, audio, text, fonts, and web/UI files
- Built-in editor for CSS, HTML, XML, JSON, and localization data
- Extract files with automatic decryption and decompression
- Right-click workflows for mesh export/import, audio export/import, and patch-to-game

### Mesh Modding

- Preview and export `.pac`, `.pam`, and `.pamlod` meshes
- Export meshes to **OBJ** and **FBX**
- Import edited OBJ files back into the game
- Supports real round-trip workflows for static meshes and PAC weapon/character mesh editing
- One-click **Import OBJ + Patch to Game**
- Matching item-name search in Explorer to find the exact live asset more easily

### Audio Modding

- Browse, search, play, export, and replace game audio
- Linked dialogue text for a large portion of voice assets
- Export to WAV/OGG
- Import WAV and patch back into the game
- Wwise-assisted WEM rebuild support

### AI Text-to-Speech

- Generate replacement voices for dialogue lines
- Supports multiple TTS providers including Edge TTS, OpenAI, ElevenLabs, Google Cloud, Azure Speech, and Mistral Voxtral
- Generate and patch audio in one workflow

### Translation Workspace

- Parse and work with more than **172,000** localization entries
- AI batch translation with multiple providers
- Prompt system designed to preserve placeholders, tags, and game terminology
- Glossary support
- Autosave and session recovery
- Game update detection and merge behavior for changed localization data

### Font Builder

- Extract game fonts
- Analyze missing glyph coverage for target languages
- Pull needed glyphs from donor fonts
- Patch updated fonts back into the game

### Ship to App

- Generate end-user install packages for translators and mod teams
- Build plug-and-play packages with `install.bat` and `uninstall.bat`
- End users do not need Python or modding tools

### Patch to Game

- Automatic backup before patching
- Rebuilds archives and checksum chains
- Handles patched or partially modified installs more safely than older tools

## Installation

### Option 1: Standalone build

If you just want to run CrimsonForge, use the standalone executable from the **GitHub Releases** page.

- Download `CrimsonForge.exe`
- Run it directly
- No separate Python install is required

### Option 2: Run from source

Requirements:

- Python `3.12+`
- Crimson Desert installed via Steam
- At least one API key if you want AI translation or TTS

Steps:

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## End User Translation Mods

CrimsonForge also supports a translator-to-player workflow.

A translator can generate a packaged mod release, and the end user only needs to:

1. Download the translator's ZIP package
2. Extract it anywhere
3. Double-click `install.bat`
4. Launch Crimson Desert

For removal, the generated package also supports uninstall via Steam file verification workflows.

## AI Providers

### Translation

- OpenAI
- Anthropic
- Google Gemini
- DeepSeek
- DeepL
- Mistral
- Cohere
- Ollama
- vLLM / custom-compatible endpoints

### Text-to-Speech

- Edge TTS
- OpenAI TTS
- ElevenLabs
- Google Cloud TTS
- Azure Speech
- Mistral Voxtral

## Building the Standalone EXE

This project includes a PyInstaller spec for a bundled Windows build:

```powershell
python -m PyInstaller --clean --noconfirm CrimsonForge.spec
```

Output:

- `dist/CrimsonForge.exe`

The build spec bundles:

- the full `data` directory
- required runtime resources
- `core/pa_checksum.dll`

## Credits

- **hzeem** - CrimsonForge author and maintainer
- **Lazorr / lazorr410** - foundational research into Pearl Abyss archive formats
- **MrIkso** - early archive and checksum tooling references
- **Altair200333** - `crimson-desert-model-browser`, helpful PAC reference and validation project

## License

This project is released under the **MIT License**.

