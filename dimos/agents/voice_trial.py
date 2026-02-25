#!/usr/bin/env python3
# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Streaming mic → text trial.

Usage:
    python voice_trial.ignore.py

Streams audio from the default mic.  When speech is detected it accumulates
audio until a short silence, then transcribes with Whisper and prints the
result.  Press Ctrl+C to stop.
"""

import sys

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHUNK_MS = 100  # milliseconds per read slice
VAD_THRESHOLD = 0.01  # RMS level that counts as speech
SILENCE_CUTOFF = 0.8  # seconds of silence before transcribing

CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_MS / 1000)


def load_whisper(model_size: str = "base"):
    try:
        import whisper  # type: ignore[import-untyped]
    except ImportError:
        print("ERROR: openai-whisper not installed.  Run:  pip install openai-whisper")
        sys.exit(1)
    print(f"Loading Whisper '{model_size}' model …")
    return whisper.load_model(model_size)


def transcribe(model, audio: np.ndarray) -> str:
    result = model.transcribe(audio, language="en", fp16=False)
    return result["text"].strip()


def main() -> None:
    model = load_whisper()
    print("Listening … (Ctrl+C to stop)\n")

    speech_buf: list[np.ndarray] = []
    silence_elapsed = 0.0
    in_speech = False

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as stream:
        while True:
            chunk, _ = stream.read(CHUNK_SAMPLES)
            mono = chunk[:, 0]
            rms = float(np.sqrt(np.mean(mono**2)))

            # live level indicator on the same line
            bars = min(int(rms * 400), 30)
            indicator = "█" * bars + "░" * (30 - bars)
            print(
                f"\r  [{indicator}] {'SPEECH' if rms >= VAD_THRESHOLD else 'silent'}  ",
                end="",
                flush=True,
            )

            if rms >= VAD_THRESHOLD:
                speech_buf.append(mono)
                silence_elapsed = 0.0
                in_speech = True
            elif in_speech:
                speech_buf.append(mono)  # include trailing silence
                silence_elapsed += CHUNK_MS / 1000

                if silence_elapsed >= SILENCE_CUTOFF:
                    # transcribe the accumulated utterance
                    audio = np.concatenate(speech_buf)
                    print()  # newline after the level bar
                    text = transcribe(model, audio)
                    if text:
                        print(f"  → {text}\n")
                    speech_buf = []
                    silence_elapsed = 0.0
                    in_speech = False


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
