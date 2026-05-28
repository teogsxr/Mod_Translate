# Voicebank decisions 2026-05-27

## Kliff

- Approved direction: `01_unique_kliff_unique_kliff_adventure_preview3`
- Active voice id for pilot generation: `UjgfAgrbkJ4UuHyxmIwr` (`Kliff Good Clone`)
- Chosen preview WAV: `C:\01-CrimsonDesertMod\08_elevenlabs_cast\previews\20260527_222330\wav\01_unique_kliff_unique_kliff_adventure_preview3.wav`
- Previous generated-design voice id: `k14eDIkv7qI0PRiCdtyg`

Notes:

- User approved the overall voice after the 12-line audition.
- The line `sound/unique_kliff_aidialogstringinfogroup_criminal_02789.wem` (`Avanti, allora. Prega il tuo dio.`) sounded slightly lower quality than the rest and must be regenerated or checked before patching.
- Do not patch a Kliff batch directly after generation. Prepare a listening montage first.
- Pronunciation rule: written `Jian` should be spoken as `Gin`. Keep `Jian` in final text/metadata, use `Gin` only for TTS phonetics. `synthesize_elevenlabs_from_manifest.py` applies this automatically and also speaks visible names from `StaticInfo` tags.
- Intro `narration` rows are voiced by Kliff in-game. Treat `sound/unique_kliff_intro_*_narration_*.wem` as Kliff for regeneration/review, not as a separate narrator voice.
- For suspicious lines, use Forge `exports/dialogue_catalog` source text first. Current Italian audio/Whisper ASR is only a secondary clue because it may already be a bad TTS clone.
- Emotion rule: do not generate shouted, panicked, angry, injured, battle, or urgent lines as neutral TTS. For these, create review variants with Eleven v3 audio tags such as `[shouts]`, `[yells]`, `[screams]`, `[angry]`, `[panicked]`, `[frantic]`, `[desperate]`, and test Italian tags such as `[urla]`, `[urla forte]`, `[grida]` when useful. Keep the final subtitle text clean; emotion tags live only in `tts_text`.
- 2026-05-28: user selected `[urla disperato] Naira! Attenta!` and `[urla disperato] Oongka! Attento!` crops as the first acceptable battle-warning style. These two were patched to `unique_kliff_intro_0100_01_player_00001` and `unique_kliff_intro_0100_02_player_00000`.
- 2026-05-28: user approved all 24 intro emotional redo lines except `09_Myurdin...!`; 23 approved lines were patched from `selected_patch_kliff_intro_emotional_approved_except_myurdin_20260528`.
- 2026-05-28: user selected `clean_style025_05__panicked___shouts__Miurdin!_Fermati!` for the remaining Myurdin line. Only the crop that says `Miurdin!` was patched, padded to the original 2.0s event, to avoid adding `Fermati!` where the subtitle/source line only contains `{Staticinfo:Knowledge:Knowledge_Myordin#Myurdin}...!`.
- 2026-05-28: bulk Kliff generation with `CDIT Kliff Adventure Candidate 03` was stopped after 3 batches / 135 lines because the user found the result too flat, like a documentary voice. New rule: regenerate Kliff with the saved ElevenLabs cloned voice `Kliff Good Clone` and overwrite those 135 lines as part of the full Kliff pass.

## Oongka

- Active voice id for review generation: `gma3zoQRrj9mVpgcxrII` (`CDIT Oongka Original Reference`)
- Source: Instant Voice Clone from original game audio extracted from `C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert\crimson_desert_it_voice_backup\20260524_111821`.
- Reference extraction/review folder: `C:\01-CrimsonDesertMod\02_voice_references\oongka_original_candidates_20260528`.
- User selected preview line 3 from `C:\01-CrimsonDesertMod\10_elevenlabs_generated_audio\oongka_ivc_preview_20260528\20260528_014005\wav_tail` as the acceptable Oongka direction.
- Direction: very deep, broad, protective, muscular warrior. Avoid "normal old man", priest-like echo, or cartoon monster. Keep Italian natural and intelligible.

## Old Provider Cleanup 2026-05-28

- Goal: replace remaining old OmniVoice-style lines for Ross/Russo, Shane, Silvan, and Whitebear with dedicated ElevenLabs voices instead of borrowing Ronnie/Conrad/Gerald/soldier voices.
- Freed ElevenLabs slots: deleted `CDIT Kliff Adventure Candidate 03` because it was rejected and replaced by `Kliff Good Clone`; deleted unused placeholder `CDIT vendor female` because it had 0 generated/patch usages and can be recreated later when the vendor pass is planned.
- Dedicated voices created from `C:\01-CrimsonDesertMod\08_elevenlabs_cast\previews\20260528_105442`: `CDIT Ross` (`l1YHi1od2NLK4KfUZMUW`), `CDIT Shane` (`uZidUikYYVKdVi00KzWq`), `CDIT Silvan` (`1Ui2JOiJW1pgJBbqNG4D`), `CDIT Whitebear` (`B5DUZDey6kzpRmVZqoFd`).
- Patch batch: `C:\01-CrimsonDesertMod\10_elevenlabs_generated_audio\old_provider_cleanup_247_manifest\20260528_110013`; model tag `eleven_v3_old_provider_cleanup_ross_shane_silvan_whitebear_20260528`; result 247 prepared and 247 patched.
- Future note: use `C:\01-CrimsonDesertMod\24_old_provider_cleanup\20260528_ross_shane_silvan_whitebear\combined_created_voices_with_cleanup_dedicated_20260528.csv` for these profiles, because the older combined CSV still contained the now-deleted `vendor_female` voice.
