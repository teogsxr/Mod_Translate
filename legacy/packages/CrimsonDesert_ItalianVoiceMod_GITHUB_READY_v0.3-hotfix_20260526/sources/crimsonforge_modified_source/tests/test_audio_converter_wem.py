from core import audio_converter


def test_strict_wem_conversion_rejects_pcm_fallback(monkeypatch, tmp_path):
    wav_path = tmp_path / "tts.wav"
    output_path = tmp_path / "tts.wem"
    wav_path.write_bytes(
        b"RIFF"
        + (36).to_bytes(4, "little")
        + b"WAVEfmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (48000).to_bytes(4, "little")
        + (96000).to_bytes(4, "little")
        + (2).to_bytes(2, "little")
        + (16).to_bytes(2, "little")
        + b"data"
        + (0).to_bytes(4, "little")
    )

    monkeypatch.setattr(audio_converter, "get_ffmpeg_path", lambda: "")

    from utils import wwise_installer

    monkeypatch.setattr(wwise_installer, "find_wwise_console", lambda: "WwiseConsole.exe")
    monkeypatch.setattr(wwise_installer, "convert_wav_to_wem_vorbis", lambda *args, **kwargs: "")

    result = audio_converter.wav_to_wem(
        str(wav_path),
        b"RIFF" + b"\x00" * 64,
        output_path=str(output_path),
        allow_pcm_fallback=False,
    )

    assert result == ""
    assert not output_path.exists()
