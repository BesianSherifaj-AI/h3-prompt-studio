# Optional local audio tools

Microphone recordings, audio references and soundtrack mixing use the existing FFmpeg installation. Keep both `ffmpeg` and `ffprobe` on `PATH`. Browser recordings in WebM are accepted even when the browser omits duration metadata; the app measures packet timestamps without changing the original recording.

To turn a recording into editable text, install the optional CPU transcription adapter from the project folder:

```powershell
uv pip install --python .venv\Scripts\python.exe -r requirements-audio.txt
```

Then choose whether to download a transcription model. This manual command caches the multilingual `small` model for this account; it needs an internet connection and can take time:

```powershell
.\.venv\Scripts\python.exe -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"
```

The app itself only opens an already cached model. It never downloads one when you press Transcribe, and uses CPU/int8 so transcription does not take H3's GPU memory. The API can also use another cached faster-whisper model name or a local compatible model folder. Restart Studio after installing the optional package.

Allow microphone access when your browser asks, finish the recording, then transcribe it. Review and edit the recognized words before using them as dialogue. Language detection is automatic unless a language is selected. The words may contain recognition errors; transcription does not prove that a generated character said the intended dialogue.

The reference library accepts audio/video clips up to two minutes. H3 conditioning uses a selected 2–15 second reference range, subject to the mode's combined reference limits. Transcription can process an already stored recording up to ten minutes, but the library upload limit still applies. Missing packages, missing cached models, an unfinished recording or undecodable audio produce an actionable error while retaining the user's original local file.

Soundtrack trim, placement, volume, fades and optional ducking create a separate mixed export. The original video and its generated audio remain unchanged. Ducking follows the original audio's volume, not a speech recognizer. Soundtrack layers are a playback mix; they do not retroactively change mouth movement in an existing video.
