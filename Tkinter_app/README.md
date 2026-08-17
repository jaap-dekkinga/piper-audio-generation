# Piper Script GUI

A tiny local desktop app that turns a multi-speaker text script into a single
MP3 file using [Piper](https://github.com/OHF-Voice/piper1-gpl) neural
text-to-speech voices. Everything runs on your machine — no cloud, no accounts.

## What it does

1. You paste (or load) a script formatted like:
   ```
   Alice: Hello there!
   Bob: Hi Alice, how are you?
   Alice: Doing great, thanks.
   ```
2. The GUI detects the speakers and lets you pick a Piper voice for each.
3. Click **Generate MP3** — it synthesizes each line, glues them together with
   a short pause between turns, and writes a single MP3.

## Requirements

- **Python 3.10+** (already installed on most systems; verify with `python --version`)
- **ffmpeg** on your PATH — needed for MP3 encoding
  - Windows: `winget install ffmpeg` (or grab a build from https://ffmpeg.org/download.html and add its `bin` folder to PATH)
  - macOS: `brew install ffmpeg`
  - Linux: `sudo apt install ffmpeg`

## Install

Open a terminal in this folder, then:

**Windows (PowerShell)**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**macOS / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Download at least one voice

The GUI has a **Download voice…** button that does this for you, but you can
also do it from the command line. Voices land in the `voices/` subfolder.

```bash
python -m piper.download_voices en_US-lessac-medium --data-dir voices
python -m piper.download_voices en_US-ryan-medium    --data-dir voices
python -m piper.download_voices en_GB-alan-medium    --data-dir voices
```

Browse all available voices with samples at
https://rhasspy.github.io/piper-samples/. Pick two or three distinct ones so
your speakers actually sound different.

## Run

```bash
python piper_gui.py
```

Then:

1. Open **example_script.txt** (or paste your own script) — click "Open script…".
2. Assign a voice to each speaker in the mapping panel.
3. Set the pause length (default 0.4 seconds).
4. Click **Generate MP3**. A file dialog remembers where to save; default is
   `output.mp3` next to the script.

## Script format details

- One turn per line: `Name: text`.
- Speaker names can contain letters, digits, spaces, `_`, and `-`.
- Blank lines are ignored.
- A line without a `Name:` prefix continues the previous speaker (useful for
  paragraph breaks inside one turn).

## Files

```
piper_gui.py         # the app
requirements.txt     # pip deps
example_script.txt   # sample two-speaker dialogue
voices/              # created on first run; put .onnx voice files here
```

## Troubleshooting

- **"No voices found"** — download at least one voice (see above).
- **MP3 export fails** — you don't have ffmpeg on your PATH. Install it, then
  reopen your terminal so the PATH updates.
- **First line takes a while** — Piper loads each voice model once and caches
  it in memory. Subsequent lines with the same voice are fast.
- **GPU** — if you want to use CUDA, install `onnxruntime-gpu` and change
  `PiperVoice.load(str(path))` in `piper_gui.py` to
  `PiperVoice.load(str(path), use_cuda=True)`.
