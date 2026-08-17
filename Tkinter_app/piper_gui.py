"""
Piper Script GUI
================
Turn a multi-speaker text script into an MP3 using local Piper TTS voices.

Script format (one line per turn):

    Alice: Hello there!
    Bob: Hi Alice, how are you?
    Alice: I'm doing great.

Blank lines are ignored. A line without a "Name:" prefix is treated as a
continuation of the previous speaker.
"""
from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path
from tkinter import (
    DoubleVar,
    END,
    Frame,
    HORIZONTAL,
    Label,
    Scale,
    StringVar,
    Text,
    Tk,
    Toplevel,
    filedialog,
    messagebox,
    ttk,
)
from tkinter.ttk import Button, Combobox, Entry, Progressbar

# ---------------------------------------------------------------------------
# Dependency checks

try:
    from piper import PiperVoice
except ImportError:
    sys.stderr.write("ERROR: piper-tts is not installed.\n"
                     "Run:  pip install piper-tts\n")
    sys.exit(1)

try:
    from pydub import AudioSegment
except ImportError:
    sys.stderr.write("ERROR: pydub is not installed.\n"
                     "Run:  pip install pydub\n"
                     "You also need ffmpeg on your PATH for MP3 export.\n")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Paths + parsing

APP_DIR = Path(__file__).parent.resolve()
VOICES_DIR = APP_DIR / "voices"
VOICES_DIR.mkdir(exist_ok=True)

# "Alice: hello"  |  "Alice Smith: hello"  |  "Alice_1: hello"
SPEAKER_LINE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_\- ]*?)\s*:\s*(.+?)\s*$")


def parse_script(text: str):
    """Yield (speaker, dialogue) tuples in order."""
    current = None
    for raw in text.splitlines():
        if not raw.strip():
            continue
        m = SPEAKER_LINE_RE.match(raw)
        if m:
            current = m.group(1).strip()
            yield current, m.group(2).strip()
        elif current:
            yield current, raw.strip()


def list_voices():
    """Return sorted [(display_name, path), ...] for .onnx files in ./voices."""
    return sorted((p.stem, p) for p in VOICES_DIR.glob("*.onnx"))


# ---------------------------------------------------------------------------
# GUI

class PiperGUI:
    def __init__(self, root: Tk):
        self.root = root
        root.title("Piper: Multi-Speaker Script -> MP3")
        root.geometry("860x720")

        self.voice_cache: dict[str, PiperVoice] = {}
        self.speaker_vars: dict[str, StringVar] = {}
        self.progress_q: queue.Queue = queue.Queue()
        self._debounce_id = None

        self._build_ui()
        self._refresh_voices()

    # --- UI ---------------------------------------------------------------
    def _build_ui(self):
        top = Frame(self.root, padx=10, pady=10)
        top.pack(fill="x")

        Button(top, text="Open script...", command=self._open_script).pack(side="left")
        Button(top, text="Save MP3 as...", command=self._pick_output).pack(side="left", padx=6)
        Button(top, text="Download voice...", command=self._download_voice).pack(side="left")
        Button(top, text="Voices folder", command=lambda: self._open_folder(VOICES_DIR)).pack(side="left", padx=6)
        Button(top, text="Refresh voices", command=self._refresh_voices).pack(side="left")

        Label(self.root, text="Script  (format:  'Name: dialogue'  per line)"
              ).pack(anchor="w", padx=10)
        self.script_text = Text(self.root, height=15, wrap="word", undo=True,
                                font=("Consolas", 10))
        self.script_text.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self.script_text.insert(
            "1.0",
            "Alice: Hello there! How are you today?\n"
            "Bob: I'm doing great, Alice. Thanks for asking.\n"
            "Alice: Glad to hear it. Want to grab coffee later?\n"
            "Bob: Absolutely. See you at three.\n",
        )
        self.script_text.bind("<KeyRelease>", lambda e: self._debounce_scan())

        Button(self.root, text="Scan script for speakers",
               command=self._rescan_speakers).pack(pady=4)

        self.mapping_frame = Frame(self.root, padx=10, pady=6,
                                   relief="groove", borderwidth=1)
        self.mapping_frame.pack(fill="x", padx=10)
        Label(self.mapping_frame, text="Assign a voice to each speaker:"
              ).pack(anchor="w")
        self.mapping_body = Frame(self.mapping_frame)
        self.mapping_body.pack(fill="x", pady=4)

        bottom = Frame(self.root, padx=10, pady=10)
        bottom.pack(fill="x")

        Label(bottom, text="Pause between lines (s):").pack(side="left")
        self.pause_var = DoubleVar(value=0.4)
        Scale(bottom, from_=0.0, to=2.0, resolution=0.1, orient=HORIZONTAL,
              variable=self.pause_var, length=180).pack(side="left", padx=8)

        self.output_var = StringVar(value=str(APP_DIR / "output.mp3"))
        Label(bottom, textvariable=self.output_var, fg="#555"
              ).pack(side="left", padx=10)

        self.generate_btn = Button(bottom, text="Generate MP3",
                                   command=self._start_generate)
        self.generate_btn.pack(side="right")

        self.progress = Progressbar(self.root, mode="determinate")
        self.progress.pack(fill="x", padx=10, pady=(0, 6))
        self.status_var = StringVar(value="Ready.")
        Label(self.root, textvariable=self.status_var, anchor="w"
              ).pack(fill="x", padx=10, pady=(0, 8))

    # --- Script scanning --------------------------------------------------
    def _debounce_scan(self):
        if self._debounce_id:
            self.root.after_cancel(self._debounce_id)
        self._debounce_id = self.root.after(400, self._rescan_speakers)

    def _rescan_speakers(self):
        text = self.script_text.get("1.0", END)
        seen, speakers = set(), []
        for spk, _ in parse_script(text):
            if spk not in seen:
                seen.add(spk)
                speakers.append(spk)

        for w in self.mapping_body.winfo_children():
            w.destroy()

        voice_names = [n for n, _ in self.available_voices]
        if not voice_names:
            Label(self.mapping_body,
                  text="No voices found in ./voices. Click 'Download voice...' to add one.",
                  fg="#a33").grid(row=0, column=0, sticky="w")
            return
        if not speakers:
            Label(self.mapping_body,
                  text="(No speakers detected. Add lines like 'Alice: hello'.)",
                  fg="#666").grid(row=0, column=0, sticky="w")
            return

        for i, spk in enumerate(speakers):
            Label(self.mapping_body, text=spk + "  ->", width=18,
                  anchor="e").grid(row=i, column=0, padx=4, pady=2)
            var = self.speaker_vars.get(spk) or StringVar()
            if var.get() not in voice_names:
                var.set(voice_names[i % len(voice_names)])
            self.speaker_vars[spk] = var
            Combobox(self.mapping_body, textvariable=var, values=voice_names,
                     state="readonly", width=45
                     ).grid(row=i, column=1, padx=4, pady=2, sticky="w")

    def _refresh_voices(self):
        self.available_voices = list_voices()
        self.voice_cache.clear()  # force reload if files changed
        self._rescan_speakers()

    # --- File pickers -----------------------------------------------------
    def _open_script(self):
        path = filedialog.askopenfilename(
            title="Open script",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        with open(path, "r", encoding="utf-8") as f:
            self.script_text.delete("1.0", END)
            self.script_text.insert("1.0", f.read())
        self._rescan_speakers()

    def _pick_output(self):
        path = filedialog.asksaveasfilename(
            title="Save MP3 as", defaultextension=".mp3",
            filetypes=[("MP3 audio", "*.mp3")])
        if path:
            self.output_var.set(path)

    def _open_folder(self, path: Path):
        p = str(path)
        try:
            if sys.platform == "win32":
                os.startfile(p)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", p])
            else:
                subprocess.Popen(["xdg-open", p])
        except Exception as e:
            messagebox.showerror("Cannot open folder", str(e))

    # --- Voice download ---------------------------------------------------
    def _download_voice(self):
        dlg = Toplevel(self.root)
        dlg.title("Download voice")
        dlg.geometry("420x180")
        Label(dlg, text="Voice name (e.g. en_US-lessac-medium)"
              ).pack(padx=10, pady=(12, 2))
        var = StringVar(value="en_US-lessac-medium")
        entry = Entry(dlg, textvariable=var, width=42)
        entry.pack(padx=10)
        entry.focus_set()
        status = StringVar(
            value="Browse names at https://rhasspy.github.io/piper-samples/")
        Label(dlg, textvariable=status, fg="#666", wraplength=380
              ).pack(padx=10, pady=8)

        def do_download():
            name = var.get().strip()
            if not name:
                return
            status.set(f"Downloading {name}... (this can take a minute)")
            dlg.update_idletasks()

            def run():
                try:
                    subprocess.run(
                        [sys.executable, "-m", "piper.download_voices", name,
                         "--data-dir", str(VOICES_DIR)],
                        check=True,
                    )
                    self.root.after(0, lambda: status.set(f"Downloaded {name}."))
                    self.root.after(0, self._refresh_voices)
                except subprocess.CalledProcessError as e:
                    self.root.after(0, lambda: status.set(f"Failed: {e}"))

            threading.Thread(target=run, daemon=True).start()

        Button(dlg, text="Download", command=do_download).pack(pady=(0, 12))

    # --- Generation -------------------------------------------------------
    def _get_voice(self, name: str) -> PiperVoice:
        if name not in self.voice_cache:
            path = next((p for n, p in self.available_voices if n == name), None)
            if not path:
                raise FileNotFoundError(f"Voice '{name}' not in {VOICES_DIR}")
            self.voice_cache[name] = PiperVoice.load(str(path))
        return self.voice_cache[name]

    def _start_generate(self):
        text = self.script_text.get("1.0", END)
        lines = list(parse_script(text))
        if not lines:
            messagebox.showerror(
                "Nothing to speak",
                "Script is empty or has no 'Name: text' lines.")
            return
        missing = {spk for spk, _ in lines
                   if not self.speaker_vars.get(spk)
                   or not self.speaker_vars[spk].get()}
        if missing:
            messagebox.showerror(
                "Voices unassigned",
                "Assign a voice for: " + ", ".join(sorted(missing)))
            return

        output = self.output_var.get()
        if not output.lower().endswith(".mp3"):
            output += ".mp3"
            self.output_var.set(output)

        pause_ms = int(self.pause_var.get() * 1000)
        self.generate_btn.config(state="disabled")
        self.progress["value"] = 0
        self.progress["maximum"] = len(lines)

        threading.Thread(
            target=self._generate,
            args=(lines, output, pause_ms),
            daemon=True,
        ).start()
        self.root.after(100, self._pump_progress)

    def _generate(self, lines, output, pause_ms):
        try:
            tmpdir = Path(tempfile.mkdtemp(prefix="piper_"))
            combined = AudioSegment.silent(duration=0)
            silence = AudioSegment.silent(duration=pause_ms)

            for i, (spk, txt) in enumerate(lines, 1):
                snippet = txt if len(txt) <= 40 else txt[:40] + "..."
                self.progress_q.put(
                    ("status", f"[{i}/{len(lines)}] {spk}: {snippet}"))
                voice_name = self.speaker_vars[spk].get()
                voice = self._get_voice(voice_name)
                wav_path = tmpdir / f"line_{i:04d}.wav"
                with wave.open(str(wav_path), "wb") as wf:
                    voice.synthesize_wav(txt, wf)
                seg = AudioSegment.from_wav(str(wav_path))
                if i > 1:
                    combined += silence
                combined += seg
                self.progress_q.put(("tick", i))

            self.progress_q.put(("status", "Encoding MP3..."))
            combined.export(output, format="mp3", bitrate="192k")
            self.progress_q.put(("done", output))
        except Exception as e:
            self.progress_q.put(("error", f"{type(e).__name__}: {e}"))

    def _pump_progress(self):
        try:
            while True:
                kind, payload = self.progress_q.get_nowait()
                if kind == "status":
                    self.status_var.set(payload)
                elif kind == "tick":
                    self.progress["value"] = payload
                elif kind == "done":
                    self.status_var.set(f"Done -> {payload}")
                    self.generate_btn.config(state="normal")
                    messagebox.showinfo("Done", f"Wrote:\n{payload}")
                    return
                elif kind == "error":
                    self.status_var.set(f"Error: {payload}")
                    self.generate_btn.config(state="normal")
                    messagebox.showerror("Error", payload)
                    return
        except queue.Empty:
            pass
        self.root.after(100, self._pump_progress)


def main():
    root = Tk()
    PiperGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
