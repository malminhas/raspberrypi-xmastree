#!/usr/bin/env python3
"""
offline_voice_tree.py
=====================

A modern, offline alternative to the voice‑controlled Christmas tree from the
original `raspberrypi‑xmastree` project.  This version runs entirely on
the Raspberry Pi without any internet connectivity or AWS services.  It uses
Vosk for offline speech recognition and pyttsx3 for local text‑to‑speech (TTS),
so your festive light show continues to work even when your network is down.

Key features
------------

* Real‑time command recognition using the microphone connected to the Pi.
  Commands follow the pattern “christmas tree <command>”.  Supported
  commands include single colours (red, green, blue, yellow, orange, purple,
  white, pink, brown, black), “disco” (randomised colour cycling), and
  “phase” (synchronised colour cycling).  Commands are case insensitive.
* Speech synthesis using pyttsx3, triggered by the “speak” and
  “generate” commands.  “christmas tree speak” plays back a bundled MP3
  file or a configured default message.  “christmas tree generate <text>”
  generates speech from the provided text using the local TTS engine.
* An optional “sing” command which plays a provided music file (for
  example, “I Wish It Could Be Christmas Every Day”).
* Cooperative multitasking via Python threads: a background thread handles
  audio transcription, another drives the LED animations, and a third handles
  speech synthesis or music playback.  Shared state between threads
  determines the behaviour of the lights and audio outputs.

Before running this script you must install the required Python packages and
download a Vosk model.  See the accompanying README for full details.

"""

import json
import os
import queue
import re
import threading
import time
from pathlib import Path
from typing import Optional  # for type hints compatible with Python < 3.10

import sounddevice as sd  # Used to capture audio from the microphone # type: ignore

# Query all available devices
devices = sd.query_devices()

# Find the index of the first input device whose name includes "ReSpeaker"
respeaker_index = None
for idx, dev in enumerate(devices):
    # We want devices that have at least one input channel
    if dev['max_input_channels'] > 0 and 'respeaker' in dev['name'].lower():
        respeaker_index = idx
        break

# Fall back to the system default input device if none found
if respeaker_index is None:
    print("ReSpeaker microphone not found; using default input device.")
    sd.default.device = (None, None)
else:
    # Set the ReSpeaker as the default input device
    sd.default.device = (respeaker_index, None)
    print(f"Using ReSpeaker device #{respeaker_index}: {devices[respeaker_index]['name']}")

# Set a fixed sample rate (Vosk models typically use 16 kHz)
sd.default.samplerate = 16000

from colorzero import Color, Hue
from tree import RGBXmasTree  # Hardware driver for PiHut’s 3D Xmas tree

# Import Vosk for offline speech recognition.  A small, local model (~50 MB)
# must be downloaded separately; specify its directory via MODEL_PATH below.
from vosk import Model, KaldiRecognizer # type: ignore

# Import pyttsx3 for offline text‑to‑speech.  This uses the on‑board speech
# engine on Linux (espeak) or other operating systems.
import pyttsx3 # type: ignore

# Optional: use VLC for MP3 playback.  Install via apt (`sudo apt install vlc`)
# and install the python bindings (`pip install python‑vlc`) if needed.
import vlc # type: ignore


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

# Path to the Vosk model directory.  Download an English model such as
# `vosk‑model‑small‑en‑us‑0.15` from https://alphacephei.com/vosk/models and
# extract it into the same directory as this script, then set MODEL_PATH
# accordingly.  Alternatively, set the environment variable VOSK_MODEL_PATH.
MODEL_PATH = os.environ.get("VOSK_MODEL_PATH", str(Path(__file__).parent / "model"))

# List of colours supported by the tree.  The names correspond to Colour names
# understood by colourzero and will be matched case‑insensitively.
SUPPORTED_COLOURS = [
    "red", "green", "blue", "yellow", "orange",
    "purple", "white", "pink", "brown", "black"
]

# Commands other than colours
SUPPORTED_COMMANDS = ["disco", "phase", "speak", "generate", "sing"]

# Length (in seconds) to wait while playing the bundled MP3 via “speak”.
DEFAULT_SPEECH_DURATION = 10

# Path to the MP3 file used by the “speak” command.  Place your own audio
# file here to customise the message spoken when saying “christmas tree speak”.
SPEECH_MP3_PATH = str(Path(__file__).parent / "speech.mp3")

# Path to the song used by the “sing” command.  If left unset or the file
# does not exist, the command will be ignored.
SING_MP3_PATH = str(Path(__file__).parent / "08-I-Wish-it-Could-be-Christmas-Everyday.mp3")

# -----------------------------------------------------------------------------
# Global state shared between threads
# -----------------------------------------------------------------------------

class State:
    """A simple container for mutable state shared between threads."""

    def __init__(self):
        # Current lighting mode: one of SUPPORTED_COLOURS, "disco", "phase",
        # or "idle".  Setting to a colour produces a solid colour; "disco"
        # cycles colours randomly; "phase" cycles hues smoothly; "idle"
        # switches off all LEDs.
        self.mode = "disco"
        # Last mode before entering speak/generate so we can return to it.
        self.last_mode = "disco"
        # Text to synthesise when handling the generate command.
        self.text_to_speak = ""
        # A flag to indicate that a TTS or music playback should start.
        self.audio_event = threading.Event()
        # Type of audio event: "speak", "generate", or "sing".
        self.audio_type = None
        # Flag to signal threads to stop gracefully
        self.stop_event = threading.Event()


STATE = State()


# -----------------------------------------------------------------------------
# LED handling
# -----------------------------------------------------------------------------

class XmasTreeController(threading.Thread):
    """Thread that drives the RGB Xmas tree based on the shared state."""

    def __init__(self, tree: RGBXmasTree, state: State):
        super().__init__(daemon=True)
        self.tree = tree
        self.state = state
        # Precompute three sets of LED indices to reproduce the original
        # behaviour: the tree has 25 LEDs, group them into three sets for
        # colourful disco and phase modes.
        self.led_sets = [list(range(25)[::3]), list(range(25)[1::3]), list(range(25)[2::3])]
        # Index of the star LED at the top of the tree (GPIO numbering).  The
        # star will be white when colours are displayed and off otherwise.
        self.star_index = 3
        # Track the currently active lighting mode so that we can detect
        # transitions (e.g. switching from a solid colour back to disco).  When
        # the mode changes to "disco" we reinitialise the LED colours to
        # produce a clear transition.
        self.current_mode = state.mode

    def run(self):
        # Initial configuration: set brightness and colours.  We start in
        # whatever mode is recorded in state.mode (usually "disco").  If the
        # initial mode is disco we seed the LEDs with red/green/blue groups.
        if self.state.mode.lower() == "disco":
            colours = [Color('red'), Color('green'), Color('blue')]
            for i, leds in enumerate(self.led_sets):
                for led in leds:
                    self.tree[led].color = colours[i]
            self.tree[self.star_index].color = Color('white')
        elif self.state.mode.lower() in SUPPORTED_COLOURS:
            for leds in self.led_sets:
                for led in leds:
                    self.tree[led].color = Color(self.state.mode)
            if self.state.mode.lower() != 'black':
                self.tree[self.star_index].color = Color('white')
            else:
                self.tree[self.star_index].color = Color('black')
        elif self.state.mode.lower() == "phase":
            # For phase we also seed red/green/blue groups so that hue cycling
            # starts with distinct colours.
            colours = [Color('red'), Color('green'), Color('blue')]
            for i, leds in enumerate(self.led_sets):
                for led in leds:
                    self.tree[led].color = colours[i]
            self.tree[self.star_index].color = Color('white')

        try:
            while not self.state.stop_event.is_set():
                try:
                    mode = self.state.mode.lower()
                    # Detect transitions between modes.  When switching into
                    # disco mode from any other mode we reset the LED groups to
                    # red/green/blue to make the change obvious.  Without this
                    # reset the hue cycling simply continues from the previous
                    # colours and may appear stuck.
                    if mode != self.current_mode:
                        if mode == "disco":
                            colours = [Color('red'), Color('green'), Color('blue')]
                            for i, leds in enumerate(self.led_sets):
                                for led in leds:
                                    self.tree[led].color = colours[i]
                            self.tree[self.star_index].color = Color('white')
                        elif mode in SUPPORTED_COLOURS:
                            for leds in self.led_sets:
                                for led in leds:
                                    self.tree[led].color = Color(mode)
                            if mode != 'black':
                                self.tree[self.star_index].color = Color('white')
                            else:
                                self.tree[self.star_index].color = Color('black')
                        elif mode == "phase":
                            colours = [Color('red'), Color('green'), Color('blue')]
                            for i, leds in enumerate(self.led_sets):
                                for led in leds:
                                    self.tree[led].color = colours[i]
                            self.tree[self.star_index].color = Color('white')
                        elif mode == "idle":
                            # idle handled below; LEDs will be turned off
                            pass
                        self.current_mode = mode
                    if mode == "disco":
                        # Cycle colours randomly across LED sets, similar to my‑tree.py
                        for leds in self.led_sets:
                            for led in leds:
                                self.tree[led].color += Hue(deg=10)
                        self.tree[self.star_index].color = Color('white')
                    elif mode == "phase":
                        # Cycle hues synchronously across all LEDs
                        for leds in self.led_sets:
                            for led in leds:
                                self.tree[led].color += Hue(deg=10)
                        self.tree[self.star_index].color = Color('white')
                    elif mode in SUPPORTED_COLOURS:
                        # Solid colour across all LEDs; except the star stays white
                        for leds in self.led_sets:
                            for led in leds:
                                self.tree[led].color = Color(mode)
                        if mode != 'black':
                            self.tree[self.star_index].color = Color('white')
                        else:
                            self.tree[self.star_index].color = Color('black')
                    elif mode == "idle":
                        # Turn off all LEDs during audio playback
                        for leds in self.led_sets:
                            for led in leds:
                                self.tree[led].color = Color('black')
                        self.tree[self.star_index].color = Color('black')
                except (AttributeError, RuntimeError) as e:
                    # GPIO has been closed, exit gracefully
                    if "NoneType" in str(e) or "off" in str(e).lower():
                        break
                    raise
                # Sleep briefly to yield control to other threads
                time.sleep(0.05)
        finally:
            # Ensure the tree is turned off cleanly when the thread exits
            try:
                self.tree.close()
            except:
                pass  # Tree may already be closed


# -----------------------------------------------------------------------------
# Audio playback and speech synthesis
# -----------------------------------------------------------------------------

class AudioController(threading.Thread):
    """Thread that handles speech synthesis and music playback."""

    def __init__(self, state: State):
        super().__init__(daemon=True)
        self.state = state
        # Initialise the TTS engine once; on Linux this uses espeak via
        # pyttsx3.  Adjust rate and volume as desired.
        self.engine = pyttsx3.init()
        rate = self.engine.getProperty('rate')
        self.engine.setProperty('rate', rate - 25)
        self.engine.setProperty('volume', 1.0)

    def play_mp3(self, path: str, duration: Optional[float] = None) -> None:
        """Play an MP3 file using VLC.  Duration can limit playback time."""
        try:
            if not os.path.exists(path):
                print(f"Audio file '{path}' not found; skipping playback")
                return
            player = vlc.MediaPlayer(path)
            player.play()
            # If a duration is specified, sleep until time is up then stop
            if duration is not None:
                time.sleep(duration)
                player.stop()
            else:
                # Wait until playback finishes
                while player.get_state() != vlc.State.Ended:
                    time.sleep(0.1)
        except Exception as exc:
            print(f"Error playing MP3 '{path}': {exc}")

    def speak_text(self, text: str):
        """Speak the supplied text using the local TTS engine."""
        try:
            print(f"Speaking: {text}")
            self.engine.say(text)
            self.engine.runAndWait()
        except Exception as exc:
            print(f"Error during TTS: {exc}")

    def run(self):
        while not self.state.stop_event.is_set():
            # Wait for a signal from the voice recognition thread (with timeout to check stop_event)
            if self.state.audio_event.wait(timeout=0.5):
                # Enter idle lighting mode during audio playback
                self.state.last_mode = self.state.mode
                self.state.mode = "idle"
                try:
                    if self.state.audio_type == "speak":
                        # Play the bundled speech MP3
                        self.play_mp3(SPEECH_MP3_PATH, DEFAULT_SPEECH_DURATION)
                    elif self.state.audio_type == "sing":
                        # Play the configured song if present
                        self.play_mp3(SING_MP3_PATH)
                    elif self.state.audio_type == "generate":
                        # Use TTS to speak arbitrary text
                        self.speak_text(self.state.text_to_speak)
                    else:
                        print(f"Unknown audio type: {self.state.audio_type}")
                finally:
                    # Restore previous lighting mode and clear the event
                    if not self.state.stop_event.is_set():
                        self.state.mode = self.state.last_mode
                    self.state.audio_type = None
                    self.state.text_to_speak = ""
                    self.state.audio_event.clear()


# -----------------------------------------------------------------------------
# Voice recognition
# -----------------------------------------------------------------------------

class VoiceRecognizer(threading.Thread):
    """Thread that listens to the microphone and updates shared state."""

    def __init__(self, state: State):
        super().__init__(daemon=True)
        self.state = state
        # Load the Vosk model.  This is an expensive operation and should be
        # performed once during initialisation.
        model_path = Path(MODEL_PATH)
        if not model_path.exists():
            raise RuntimeError(
                f"Vosk model not found at '{model_path}'.\n"
                "Download an English model (e.g. vosk-model-small-en-us-0.15)\n"
                "and extract it so that MODEL_PATH points to the model directory."
            )
        self.model = Model(str(model_path))
        # Limit vocabulary to improve recognition accuracy.  We include only
        # phrases starting with "christmas tree" followed by a known command or
        # colour.  Additional words after the command are captured by the regex.
        grammar_phrases = [f"christmas tree {cmd}" for cmd in SUPPORTED_COLOURS + SUPPORTED_COMMANDS]
        self.recognizer = KaldiRecognizer(self.model, 16000, json.dumps(grammar_phrases))

        # Use a queue to transfer audio blocks from the callback to the recognizer
        self.q: queue.Queue[bytes] = queue.Queue()

        # Regex pattern to capture commands.  It looks for "christmas tree" at
        # the start and extracts the first word after it (the command) and
        # everything that follows (for generate).
        self.command_pattern = re.compile(r"christmas tree\s+(\w+)(?:\s+(.*))?")

    def audio_callback(self, indata, frames, time_info, status):
        if status:
            print(status)
        # Convert the recorded bytes to raw bytes and push them to the queue
        self.q.put(bytes(indata))

    def run(self):
        # Determine the default sample rate.  Vosk requires 16 kHz, but
        # sounddevice will resample automatically if needed.
        device_info = sd.query_devices(sd.default.device[0], 'input')
        samplerate = int(device_info['default_samplerate'])
        print(f"Initialising audio input at {samplerate} Hz (device {sd.default.device[0]})")
        # Start the microphone stream
        with sd.RawInputStream(samplerate=samplerate,
                               blocksize = 8000,
                               dtype='int16',
                               channels=1,
                               callback=self.audio_callback):
            while not self.state.stop_event.is_set():
                try:
                    # Use timeout to allow checking stop_event periodically
                    data = self.q.get(timeout=0.1)
                    if self.recognizer.AcceptWaveform(data):
                        # Parse the full result JSON
                        result = json.loads(self.recognizer.Result())
                        text = result.get('text', '').strip().lower()
                        if text:
                            self.process_command(text)
                    else:
                        # Partial results are not used here, but could be printed
                        pass
                except queue.Empty:
                    # Timeout allows checking stop_event
                    continue

    def process_command(self, utterance: str):
        """Interpret a recognised utterance and update the shared state."""
        print(f"Recognised utterance: '{utterance}'")
        m = self.command_pattern.match(utterance)
        if not m:
            print("Utterance does not match command pattern; ignoring")
            return
        command = m.group(1).lower()
        rest = m.group(2) or ""
        # Handle colour commands
        if command in SUPPORTED_COLOURS:
            print(f"Setting mode to colour '{command}'")
            self.state.mode = command
            return
        # Disco and phase modes
        if command in ["disco", "phase"]:
            print(f"Setting mode to '{command}'")
            self.state.mode = command
            return
        # Speak: play the bundled MP3
        if command == "speak":
            print("Preparing to play bundled speech")
            self.state.audio_type = "speak"
            self.state.audio_event.set()
            return
        # Sing: play the configured song
        if command == "sing":
            print("Preparing to play the configured song")
            self.state.audio_type = "sing"
            self.state.audio_event.set()
            return
        # Generate: use TTS to speak the rest of the sentence
        if command == "generate":
            generated_text = rest.strip()
            if len(generated_text) < 1:
                print("No text provided for generate command; ignoring")
                return
            print(f"Preparing to generate speech for: '{generated_text}'")
            self.state.text_to_speak = generated_text
            self.state.audio_type = "generate"
            self.state.audio_event.set()
            return
        print(f"Unrecognised command '{command}'; ignoring")


# -----------------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------------

def main() -> int:
    """Entry point for the offline voice‑controlled Christmas tree."""
    print("Starting offline voice‑controlled Christmas tree…")
    # Instantiate the hardware tree.  Set brightness to a reasonable default.
    tree = RGBXmasTree(brightness=0.1)
    # Create and start the worker threads
    led_thread = XmasTreeController(tree, STATE)
    audio_thread = AudioController(STATE)
    voice_thread = VoiceRecognizer(STATE)
    led_thread.start()
    audio_thread.start()
    voice_thread.start()
    try:
        # Keep the main thread alive until interrupted
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping…")
        # Signal all threads to stop
        STATE.stop_event.set()
        # Wait for threads to finish (with timeout)
        led_thread.join(timeout=1.0)
        audio_thread.join(timeout=1.0)
        voice_thread.join(timeout=1.0)
        return 0
    finally:
        # Close the tree after threads have stopped
        try:
            tree.close()
        except:
            pass  # Tree may already be closed


if __name__ == '__main__':
    raise SystemExit(main())
