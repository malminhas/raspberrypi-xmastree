#!/usr/bin/env python3
"""
offline_voice_tree.py
=====================

A modern, offline alternative to the voice-controlled Christmas tree from the
original `raspberrypi-xmastree` project.  This version runs entirely on
the Raspberry Pi without any internet connectivity or AWS services.  It uses
Vosk for offline speech recognition and pyttsx3 for local text-to-speech (TTS),
so your festive light show continues to work even when your network is down.

Key features
------------

* Real-time command recognition using the microphone connected to the Pi.
  Commands follow the pattern “christmas tree <command>”.  Supported
  commands include single colours (red, green, blue, yellow, orange, purple,
  white, pink, brown, black), “disco” (randomised colour cycling), and
  “phase” (synchronised colour cycling).  Commands are case insensitive.
* Speech synthesis using pyttsx3, triggered by the “speak” and
  “generate” commands.  “christmas tree speak” plays back a bundled MP3
  file or a configured default message.  “christmas tree generate <text>”
  generates speech from the provided text using the local TTS engine.
* An optional “sing” command which plays a provided music file (for
  example, “I Wish It Could Be Christmas Every Day”).
* AI-powered entertainment commands (optional, requires GreenPT API):
  "christmas tree joke" fetches and speaks a family-friendly joke, while
  "christmas tree flatter" generates and speaks over-the-top humorous praise.
  These commands require internet connectivity and an API key but gracefully
  degrade if unavailable.
* Cooperative multitasking via Python threads: a background thread handles
  audio transcription, another drives the LED animations, and a third handles
  speech synthesis or music playback.  Shared state between threads
  determines the behaviour of the lights and audio outputs.

Before running this script you must install the required Python packages and
download a Vosk model.  See the accompanying README for full details.

"""

import argparse
import json
import os
import queue
import random
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional, Any  # for type hints compatible with Python < 3.10

try:
    from dotenv import load_dotenv  # type: ignore
except ImportError:
    # dotenv is optional - if not installed, skip loading .env file
    def load_dotenv(*args, **kwargs):
        pass

from colorzero import Color, Hue # type: ignore
from tree import RGBXmasTree  # Hardware driver for PiHut's 3D Xmas tree
# Import Vosk for offline speech recognition.  A small, local model (~50 MB)
# must be downloaded separately; specify its directory via MODEL_PATH below.
from vosk import Model, KaldiRecognizer # type: ignore
# Import pyttsx3 for offline text‑to‑speech.  This uses the on‑board speech
# engine on Linux (espeak) or other operating systems.
import pyttsx3 # type: ignore
# Optional: use VLC for MP3 playback.  Install via apt (`sudo apt install vlc`)
# and install the python bindings (`pip install python‑vlc`) if needed.
import vlc # type: ignore
import sounddevice as sd  # Used to capture audio from the microphone # type: ignore

# Query all available devices
devices = sd.query_devices()

# Find the index of the first input device whose name includes "ReSpeaker"
respeaker_index = None
respeaker_alsa_card = None
for idx, dev in enumerate(devices):
    # We want devices that have at least one input channel
    if dev['max_input_channels'] > 0 and 'respeaker' in dev['name'].lower():
        respeaker_index = idx
        # Extract ALSA card number from device name (format: hw:X,0)
        match = re.search(r'hw:(\d+),0', dev['name'])
        if match:
            respeaker_alsa_card = match.group(1)
        break

# Fall back to the system default input device if none found
if respeaker_index is None:
    sd.default.device = (None, None)
else:
    # Set the ReSpeaker as the default input device
    sd.default.device = (respeaker_index, None)

# Set a fixed sample rate (Vosk models typically use 16 kHz)
sd.default.samplerate = 16000

# -----------------------------------------------------------------------------
# Load environment variables from local.env
# -----------------------------------------------------------------------------

# Load environment variables from local.env file (if it exists)
# This happens at module import time, before reading the env vars below
# Existing environment variables take precedence (override=False)
try:
    _local_env_path = Path(__file__).parent / "local.env"
    if _local_env_path.exists():
        # Convert Path to string for load_dotenv
        load_dotenv(str(_local_env_path), override=False)
        # Note: override=False means existing env vars take precedence
except (NameError, AttributeError):
    # __file__ might not be defined in some contexts (e.g., interactive Python)
    # Try loading from current directory as fallback
    _local_env_path = Path("local.env")
    if _local_env_path.exists():
        load_dotenv(str(_local_env_path), override=False)

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
SUPPORTED_COMMANDS = ["disco", "phase", "speak", "generate", "sing", "joke", "flatter", "gb"]

# Length (in seconds) to wait while playing the bundled MP3 via “speak”.
DEFAULT_SPEECH_DURATION = 10

# Path to the MP3 file used by the “speak” command.  Place your own audio
# file here to customise the message spoken when saying “christmas tree speak”.
SPEECH_MP3_PATH = str(Path(__file__).parent / "speech.mp3")

# Path to the song used by the "sing" command.  If left unset or the file
# does not exist, the command will be ignored.
SING_MP3_PATH = str(Path(__file__).parent / "08-I-Wish-it-Could-be-Christmas-Everyday.mp3")

# Default text to speak when "generate" command is used (since grammar prevents capturing text)
DEFAULT_GENERATE_TEXT = "Hello everyone, this is your Christmas tree talking"

# Hardcoded joke text if JOKE_TEXT environment variable is set (overrides API)
HARDCODED_JOKE = os.environ.get("JOKE_TEXT", None)

# LLM provider functions - will be set at runtime based on command line argument
get_joke = None
get_flattery = None


# -----------------------------------------------------------------------------
# Global state shared between threads
# -----------------------------------------------------------------------------

class State:
    """A simple container for mutable state shared between threads.
    
    This class holds the shared state that is accessed and modified by
    multiple threads (voice recognition, LED control, and audio playback).
    """

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
        # Track jokes told during this session to avoid repetition
        self.previous_jokes = []
        # Track flattery given during this session to avoid repetition
        self.previous_flattery = []


STATE = State()


# -----------------------------------------------------------------------------
# LED handling
# -----------------------------------------------------------------------------

class XmasTreeController(threading.Thread):
    """Thread that drives the RGB Xmas tree based on the shared state.
    
    This thread continuously updates the LED colors and patterns based on
    the current mode stored in the shared state. It handles disco mode
    (random color cycling), phase mode (synchronized hue cycling), solid
    colors, and idle mode (all LEDs off).
    """

    def __init__(self, tree: RGBXmasTree, state: State):
        """Initialize the LED controller thread.
        
        Args:
            tree: The RGBXmasTree hardware interface
            state: Shared state object for inter-thread communication
        """
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

    def run(self) -> None:
        """Main thread loop that continuously updates LED colors based on state.
        
        Handles mode transitions and implements the various lighting patterns
        (disco, phase, solid colors, idle). Runs until stop_event is set.
        """
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
        elif self.state.mode.lower() == "sparkle":
            # Sparkle mode: initialize with random colors
            colors = ['red', 'green', 'blue', 'yellow', 'orange', 'purple', 'white', 'pink']
            for led in range(25):
                self.tree[led].color = Color(random.choice(colors))
            self.tree[self.star_index].color = Color('white')
        elif self.state.mode.lower() == "geebee":
            # GB flag pattern: Union Jack approximation with red, white, and blue
            # Pattern approximates the Union Jack with red crosses on blue/white background
            # 5x5 grid: center is red (cross intersection), diagonals suggest flag design
            gb_pattern = [
                'blue', 'white', 'red', 'white', 'blue',    # Row 1 (top)
                'white', 'blue', 'red', 'blue', 'white',    # Row 2
                'red', 'red', 'red', 'red', 'red',          # Row 3 (middle - horizontal red cross)
                'white', 'blue', 'red', 'blue', 'white',    # Row 4
                'blue', 'white', 'red', 'white', 'blue'     # Row 5 (bottom)
            ]
            for led in range(25):
                self.tree[led].color = Color(gb_pattern[led])
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
                        elif mode == "sparkle":
                            # Sparkle mode: initialize with random colors
                            colors = ['red', 'green', 'blue', 'yellow', 'orange', 'purple', 'white', 'pink']
                            for led in range(25):
                                self.tree[led].color = Color(random.choice(colors))
                            self.tree[self.star_index].color = Color('white')
                        elif mode == "geebee":
                            # GB flag pattern: Union Jack approximation
                            gb_pattern = [
                                'blue', 'white', 'red', 'white', 'blue',
                                'white', 'blue', 'red', 'blue', 'white',
                                'red', 'red', 'red', 'red', 'red',
                                'white', 'blue', 'red', 'blue', 'white',
                                'blue', 'white', 'red', 'white', 'blue'
                            ]
                            for led in range(25):
                                self.tree[led].color = Color(gb_pattern[led])
                            self.tree[self.star_index].color = Color('white')
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
                    elif mode == "sparkle":
                        # Sparkle effect: dynamically twinkle LEDs with random bright colors
                        bright_colors = [Color('red'), Color('green'), Color('blue'), Color('yellow'), 
                                        Color('orange'), Color('purple'), Color('white'), Color('pink')]
                        dim_colors = [Color('darkred'), Color('darkgreen'), Color('darkblue'), 
                                     Color('darkorange'), Color('darkviolet')]
                        # Each frame, randomly assign LEDs to be bright, dim, or off
                        for led in range(25):
                            rand = random.random()
                            if rand < 0.25:
                                # ~25% bright sparkle
                                self.tree[led].color = random.choice(bright_colors)
                            elif rand < 0.5:
                                # ~25% dimmed color
                                self.tree[led].color = random.choice(dim_colors)
                            else:
                                # ~50% off (black)
                                self.tree[led].color = Color('black')
                        # Star twinkles between white and dimmed
                        if random.random() < 0.7:
                            self.tree[self.star_index].color = Color('white')
                        else:
                            self.tree[self.star_index].color = Color('gray')
                    elif mode == "geebee":
                        # GB flag pattern: static Union Jack approximation
                        # Pattern remains static (no animation needed for flag)
                        gb_pattern = [
                            'blue', 'white', 'red', 'white', 'blue',
                            'white', 'blue', 'red', 'blue', 'white',
                            'red', 'red', 'red', 'red', 'red',
                            'white', 'blue', 'red', 'blue', 'white',
                            'blue', 'white', 'red', 'white', 'blue'
                        ]
                        for led in range(25):
                            self.tree[led].color = Color(gb_pattern[led])
                        self.tree[self.star_index].color = Color('white')
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
    """Thread that handles speech synthesis and music playback.
    
    This thread manages audio output including TTS generation (using either
    Piper TTS or pyttsx3), MP3 playback, and WAV file playback. It responds
    to audio events set by the voice recognition thread.
    """

    def __init__(self, state: State, tts_preference: Optional[str] = None):
        """Initialize the audio controller thread.
        
        Args:
            state: Shared state object for inter-thread communication
            tts_preference: TTS engine preference - "piper", "pyttsx3", or None (auto-detect)
        """
        super().__init__(daemon=True)
        self.state = state
        # tts_preference: "piper", "pyttsx3", or None (auto-detect)
        self.tts_preference = tts_preference
        
        # Piper TTS configuration
        self.piper_executable = None
        self.piper_model_path = None
        self.use_piper = False
        
        # pyttsx3 engine (initialized only if needed)
        self.engine = None
        
        # Configure and select TTS engine
        self._configure_piper()
        self._configure_pyttsx3()
        self._select_tts_engine()
        
        # Store TTS info for configuration summary
        self.tts_engine_name = None
        self.tts_voice_or_model = None
        self._store_tts_info()
    
    def _configure_piper(self) -> None:
        """Configure Piper TTS by finding executable and model path.
        
        Searches common installation locations for the Piper executable and
        reads the model path from the PIPER_MODEL_PATH environment variable.
        The environment variables can be set in local.env or as system environment variables.
        
        Requirements:
        - Piper executable must be installed and accessible via PATH, PIPER_EXECUTABLE_PATH, or in common locations
        - PIPER_MODEL_PATH environment variable must be set to the .onnx model file path
        
        Environment variables:
        - PIPER_EXECUTABLE_PATH: Explicit path to piper executable (checked first)
        - PIPER_MODEL_PATH: Path to the .onnx model file
        """
        import shutil
        
        # First check for explicit path from environment variable
        explicit_path = os.environ.get("PIPER_EXECUTABLE_PATH", None)
        if explicit_path:
            # Expand shell variables like $HOME and ~
            explicit_path = os.path.expanduser(os.path.expandvars(explicit_path))
            if os.path.isfile(explicit_path) and os.access(explicit_path, os.X_OK):
                self.piper_executable = explicit_path
        else:
            # Check multiple possible locations for piper executable
            possible_piper_paths = [
                shutil.which("piper"),  # Check PATH first
                "/usr/local/bin/piper/piper",  # Common subdirectory installation
                "/usr/local/bin/piper",
                "/usr/bin/piper",
                os.path.expanduser("~/.local/bin/piper"),
                "/usr/local/piper/piper",
            ]
            
            # Find piper executable
            for piper_path in possible_piper_paths:
                if piper_path and os.path.isfile(piper_path) and os.access(piper_path, os.X_OK):
                    self.piper_executable = piper_path
                    break
        
        # Get model path from environment and expand shell variables like $HOME and ~
        self.piper_model_path = os.environ.get("PIPER_MODEL_PATH", None)
        if self.piper_model_path:
            # Expand $HOME and other environment variables, and ~
            self.piper_model_path = os.path.expanduser(os.path.expandvars(self.piper_model_path))
    
    def _configure_pyttsx3(self) -> None:
        """Configure pyttsx3 TTS engine with English voice selection.
        
        Initializes the pyttsx3 engine and selects the best available English
        voice, prioritizing mbrola voices for better quality. Also configures
        speech rate and volume settings.
        """
        # Initialize the TTS engine
        self.engine = pyttsx3.init()
        
        # Try to select an English voice if available
        voices = self.engine.getProperty('voices')
        if voices:
            # Priority: English mbrola > English non-mbrola > any mbrola > any English > default
            english_mbrola = None
            english_voice = None
            any_mbrola = None
            any_english = None
            
            for voice in voices:
                name_lower = voice.name.lower()
                id_lower = voice.id.lower() if hasattr(voice, 'id') else ''
                
                # Check if it's English (common patterns: en, en_us, en-gb, english, etc.)
                is_english = any(marker in name_lower or marker in id_lower 
                                for marker in ['en', 'english', 'en_us', 'en-gb', 'en_gb', 'en-us'])
                is_mbrola = 'mbrola' in name_lower or 'mb-' in name_lower
                
                if is_english and is_mbrola:
                    english_mbrola = voice
                elif is_english and not english_voice:
                    english_voice = voice
                elif is_mbrola and not any_mbrola:
                    any_mbrola = voice
                elif is_english and not any_english:
                    any_english = voice
            
            # Select best available voice
            selected_voice = english_mbrola or english_voice or any_mbrola or any_english
            
            if selected_voice:
                self.engine.setProperty('voice', selected_voice.id)
                print(f"[TTS] Using pyttsx3 voice: {selected_voice.name}")
        
        # Adjust rate - slower is often clearer and less tinny
        rate = self.engine.getProperty('rate')
        self.engine.setProperty('rate', max(100, rate - 50))  # Slower rate, minimum 100
        self.engine.setProperty('volume', 1.0)
        
        # Try to improve quality by using better espeak parameters if available
        os.environ.setdefault('ESPEAK_DATA_PATH', '/usr/share/espeak-ng-data')
    
    def _select_tts_engine(self) -> None:
        """Select which TTS engine to use based on preference and availability.
        
        Determines whether to use Piper TTS or pyttsx3 based on user preference
        and what's available on the system. Sets the use_piper flag accordingly.
        """
        piper_available = (self.piper_executable is not None and 
                          self.piper_model_path is not None and 
                          os.path.exists(self.piper_model_path))
        
        if self.tts_preference == "pyttsx3":
            # User explicitly requested pyttsx3
            self.use_piper = False
            print("[TTS] Using pyttsx3 (user preference)")
        elif self.tts_preference == "piper":
            # User explicitly requested Piper
            if piper_available:
                self.use_piper = True
                print(f"[TTS] Using Piper TTS: {os.path.basename(self.piper_model_path)}")
            else:
                self.use_piper = False
                print("[TTS] Warning: Piper requested but not available, falling back to pyttsx3")
                if not self.piper_executable:
                    print("  Piper executable not found.")
                    print("  Options:")
                    print("    1. Set PIPER_EXECUTABLE_PATH in local.env, e.g.:")
                    print("       export PIPER_EXECUTABLE_PATH=\"/usr/local/bin/piper/piper\"")
                    print("    2. Install to a standard location:")
                    print("       Download from https://github.com/rhasspy/piper/releases")
                    print("       Then: sudo mv piper /usr/local/bin/piper && sudo chmod +x /usr/local/bin/piper")
                    print("    3. Add piper to your PATH")
                elif not self.piper_model_path:
                    print("  PIPER_MODEL_PATH environment variable not set.")
                    print("  Set it in local.env or as an environment variable, e.g.:")
                    print("  export PIPER_MODEL_PATH=\"$HOME/.local/share/piper/models/en_US-lessac-medium.onnx\"")
                    print("  Note: $HOME will be automatically expanded")
                elif not os.path.exists(self.piper_model_path):
                    print(f"  Piper model file not found: {self.piper_model_path}")
                    print(f"  Expanded from: {os.environ.get('PIPER_MODEL_PATH', 'not set')}")
                    print("  Download a model from: https://huggingface.co/rhasspy/piper-voices")
                    print("  Or check that the path in local.env is correct")
        else:
            # Auto-detect: prefer Piper if available, otherwise pyttsx3
            if piper_available:
                self.use_piper = True
                print(f"[TTS] Using Piper TTS: {os.path.basename(self.piper_model_path)}")
            else:
                self.use_piper = False
                print("[TTS] Using pyttsx3 (Piper not available)")
    
    def _store_tts_info(self) -> None:
        """Store TTS engine and voice/model info for configuration summary."""
        if self.use_piper:
            self.tts_engine_name = "Piper TTS"
            if self.piper_model_path:
                self.tts_voice_or_model = os.path.basename(self.piper_model_path).replace('.onnx', '')
            else:
                self.tts_voice_or_model = "(unknown)"
        else:
            self.tts_engine_name = "pyttsx3"
            if self.engine:
                try:
                    current_voice = self.engine.getProperty('voice')
                    voices = self.engine.getProperty('voices')
                    if voices and current_voice:
                        for voice in voices:
                            if voice.id == current_voice:
                                self.tts_voice_or_model = voice.name
                                break
                    if not self.tts_voice_or_model:
                        self.tts_voice_or_model = "(default)"
                except:
                    self.tts_voice_or_model = "(default)"
            else:
                self.tts_voice_or_model = "(default)"

    def play_mp3(self, path: str, duration: Optional[float] = None) -> None:
        """Play an MP3 or WAV file using VLC on the ReSpeaker 4-Mic board.
        
        Args:
            path: Path to the audio file (MP3 or WAV)
            duration: Optional duration in seconds to limit playback time.
                     If None, plays the entire file.
        
        This implementation uses ALSA's ``plughw`` device rather than the raw
        ``hw`` device. The ``hw`` plugin presents the hardware without any
        conversions, so if the audio file's sample rate, bit depth, or channel
        count is not supported by the ReSpeaker's DAC, VLC will fail with
        messages such as "no supported sample format" and "failed to create
        audio output". In contrast, the ``plughw`` wrapper employs the ALSA
        ``plug`` plugin, which automatically performs channel duplication,
        sample value conversion, and resampling when necessary.
        
        VLC is configured with ``--intf=dummy`` to suppress the graphical
        interface, and the volume is set to 50% (0-100 scale) using
        ``vlc.MediaPlayer.audio_set_volume()``. The player and instance are
        released when playback ends to ensure resources are freed.
        """
        try:
            # Convert to absolute path to avoid any path resolution issues
            abs_path = os.path.abspath(path)
            if not os.path.exists(abs_path):
                print(f"Audio file '{abs_path}' not found; skipping playback")
                return
            
            # Verify file is readable and has content
            if not os.access(abs_path, os.R_OK):
                print(f"Audio file '{abs_path}' is not readable; skipping playback")
                return
            
            file_size = os.path.getsize(abs_path)
            if file_size == 0:
                print(f"Audio file '{abs_path}' is empty; skipping playback")
                return

            # Create a VLC instance and media player with ALSA output directed to ReSpeaker
            # Use the ALSA card number extracted from the device detection
            alsa_device = f'plughw:{respeaker_alsa_card},0' if respeaker_alsa_card else 'plughw:2,0'
            instance = vlc.Instance('--aout=alsa', f'--alsa-audio-device={alsa_device}', '--intf=dummy')
            # Use absolute path for VLC
            media = instance.media_new(abs_path)
            player = instance.media_player_new()
            player.set_media(media)

            # Set volume (0-100 scale). Default is 75% for better audibility
            volume = int(os.environ.get("VLC_VOLUME", 75))
            player.audio_set_volume(volume)

            # Give VLC a moment to initialize before playing (reduced from 0.1s to 0.05s)
            time.sleep(0.01)
            player.play()
            
            # Wait a moment for playback to actually start
            time.sleep(0.2)
            
            # Verify playback started successfully
            state = player.get_state()
            if state == vlc.State.Error:
                print(f"VLC playback error: failed to start playback of '{abs_path}'")
                player.release()
                instance.release()
                return

            # If a duration is specified, play for that long; otherwise wait for the media to finish.
            if duration:
                time.sleep(duration)
                player.stop()
            else:
                while True:
                    state = player.get_state()
                    if state in (vlc.State.Ended, vlc.State.Error):
                        break
                    time.sleep(0.1)

            # Release resources so playback stops when the function exits
            player.release()
            instance.release()
        except Exception as exc:
            print(f"Error playing MP3 '{path}': {exc}")
        
    def speak_text(self, text: str) -> None:
        """Speak the supplied text using the local TTS engine.
        
        Args:
            text: The text to speak
        """
        try:
            print(f"Speaking: {text}")
            self.engine.say(text)
            self.engine.runAndWait()
        except Exception as exc:
            print(f"Error during TTS: {exc}")
    
    def _generate_speech_with_piper(self, text: str, output_path: str) -> bool:
        """Generate speech using Piper TTS.
        
        Args:
            text: Text to convert to speech
            output_path: Path where WAV file should be saved
            
        Returns:
            True if successful, False otherwise
        """
        try:
            result = subprocess.run(
                [self.piper_executable, "--model", self.piper_model_path, "--output_file", output_path],
                input=text,
                text=True,
                capture_output=True,
                timeout=30,
                check=True
            )
            # Verify the file was created
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return True
            else:
                raise FileNotFoundError("Piper did not generate output file")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
            print(f"[TTS] Piper failed: {exc}")
            return False
    
    def _generate_speech_with_pyttsx3(self, text: str, output_path: str) -> bool:
        """Generate speech using pyttsx3.
        
        Args:
            text: Text to convert to speech
            output_path: Path where WAV file should be saved
            
        Returns:
            True if successful, False otherwise
        """
        try:
            self.engine.save_to_file(text, output_path)
            self.engine.runAndWait()
            return True
        except Exception as exc:
            print(f"[TTS] pyttsx3 failed: {exc}")
            return False
    
    def _wait_for_audio_file(self, file_path: str, max_wait: float = 5.0) -> bool:
        """Wait for audio file to be created and have content.
        
        Args:
            file_path: Path to the audio file
            max_wait: Maximum time to wait in seconds
            
        Returns:
            True if file exists and has content, False otherwise
        """
        wait_interval = 0.1  # Check every 100ms
        waited = 0.0
        while waited < max_wait:
            if os.path.exists(file_path):
                file_size = os.path.getsize(file_path)
                if file_size > 0:
                    # File exists and has content, give it a moment to fully flush
                    time.sleep(0.2)
                    return True
            time.sleep(wait_interval)
            waited += wait_interval
        return False
    
    def generate_and_play_speech(self, text: str) -> None:
        """Generate speech from text using the selected TTS engine, save to WAV file, and play via VLC.
        
        Args:
            text: The text to convert to speech and play
            
        The method creates a temporary WAV file, generates speech using either
        Piper TTS or pyttsx3 (with automatic fallback), waits for the file to
        be ready, plays it via VLC, and cleans up the temporary file.
        """
        import tempfile
        temp_wav_path = None
        try:
            # Create a temporary WAV file
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_file:
                temp_wav_path = tmp_file.name
            
            # Generate speech using the selected engine
            success = False
            if self.use_piper:
                success = self._generate_speech_with_piper(text, temp_wav_path)
                # Fall back to pyttsx3 if Piper fails
                if not success:
                    print("[TTS] Falling back to pyttsx3")
                    success = self._generate_speech_with_pyttsx3(text, temp_wav_path)
            else:
                success = self._generate_speech_with_pyttsx3(text, temp_wav_path)
            
            if not success:
                raise RuntimeError("Failed to generate speech")
            
            # For Piper, subprocess.run already waits for completion, so minimal wait needed
            # For pyttsx3, we still need to wait for file to be written
            if self.use_piper:
                # Piper subprocess.run already completed, just verify file exists
                if not os.path.exists(temp_wav_path) or os.path.getsize(temp_wav_path) == 0:
                    raise FileNotFoundError(f"Piper did not generate output file: {temp_wav_path}")
                # Small delay to ensure file is fully flushed to disk
                time.sleep(0.05)
            else:
                # pyttsx3 may need more time for file writing
                if not self._wait_for_audio_file(temp_wav_path):
                    raise FileNotFoundError(f"Audio file was not created or is empty: {temp_wav_path}")
            
            # Play the generated WAV file using VLC
            self.play_mp3(temp_wav_path)
            
        except Exception as exc:
            print(f"Error generating/playing speech: {exc}")
        finally:
            # Clean up temporary file
            if temp_wav_path and os.path.exists(temp_wav_path):
                try:
                    os.unlink(temp_wav_path)
                except Exception:
                    pass

    def run(self) -> None:
        """Main thread loop that handles audio playback requests.
        
        Waits for audio events from the voice recognition thread and processes
        them (speech generation, MP3 playback, etc.). Runs until stop_event is set.
        """
        while not self.state.stop_event.is_set():
            # Wait for a signal from the voice recognition thread (with timeout to check stop_event)
            if self.state.audio_event.wait(timeout=0.5):
                # Enter appropriate lighting mode during audio playback
                self.state.last_mode = self.state.mode
                # Use sparkle mode for jokes, idle for other audio
                if self.state.audio_type == "joke":
                    self.state.mode = "sparkle"
                else:
                    self.state.mode = "idle"
                try:
                    if self.state.audio_type == "speak":
                        # Play the bundled speech MP3
                        self.play_mp3(SPEECH_MP3_PATH, DEFAULT_SPEECH_DURATION)
                    elif self.state.audio_type == "sing":
                        # Play the configured song if present
                        self.play_mp3(SING_MP3_PATH)
                    elif self.state.audio_type == "generate":
                        # Generate speech using pyttsx3, save to WAV, and play via VLC
                        self.generate_and_play_speech(self.state.text_to_speak)
                    elif self.state.audio_type == "joke":
                        # Use hardcoded joke if JOKE_TEXT is set, otherwise fetch from API
                        if HARDCODED_JOKE:
                            joke = HARDCODED_JOKE
                        else:
                            joke = get_joke(previous_jokes=self.state.previous_jokes)
                        if joke:
                            # Track this joke to avoid repetition (only if not hardcoded)
                            if not HARDCODED_JOKE:
                                self.state.previous_jokes.append(joke)
                                # Keep only the last 10 jokes to avoid prompt bloat
                                if len(self.state.previous_jokes) > 10:
                                    self.state.previous_jokes.pop(0)
                            self.generate_and_play_speech(joke)
                        else:
                            print("Failed to fetch joke from GreenPT API")
                    elif self.state.audio_type == "flatter":
                        # Fetch flattery from GreenPT API and speak it
                        flattery = get_flattery(previous_flattery=self.state.previous_flattery)
                        if flattery:
                            # Track this flattery to avoid repetition
                            self.state.previous_flattery.append(flattery)
                            # Keep only the last 10 flattery to avoid prompt bloat
                            if len(self.state.previous_flattery) > 10:
                                self.state.previous_flattery.pop(0)
                            self.generate_and_play_speech(flattery)
                        else:
                            print("Failed to fetch flattery from GreenPT API")
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
    """Thread that listens to the microphone and updates shared state.
    
    This thread continuously captures audio from the microphone, processes it
    through the Vosk speech recognition engine, and updates the shared state
    when valid commands are recognized.
    """

    def __init__(self, state: State):
        """Initialize the voice recognition thread.
        
        Args:
            state: Shared state object for inter-thread communication
            
        Raises:
            RuntimeError: If the Vosk model is not found at MODEL_PATH
        """
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

    def audio_callback(self, indata: Any, frames: int, time_info: Any, status: Any) -> None:
        """Callback function for sounddevice audio stream.
        
        Args:
            indata: Input audio data (numpy array)
            frames: Number of frames
            time_info: Timing information (dict)
            status: Status flags (printed if present)
            
        This callback is called by sounddevice for each audio block. It converts
        the audio data to bytes and pushes it to the queue for processing by
        the main run() loop.
        """
        if status:
            print(status)
        # Convert the recorded bytes to raw bytes and push them to the queue
        self.q.put(bytes(indata))

    def run(self) -> None:
        """Main thread loop that processes audio and recognizes commands.
        
        Continuously captures audio from the microphone, processes it through
        Vosk speech recognition, and calls process_command() when valid
        utterances are recognized. Runs until stop_event is set.
        """
        # Determine the default sample rate.  Vosk requires 16 kHz, but
        # sounddevice will resample automatically if needed.
        device_info = sd.query_devices(sd.default.device[0], 'input')
        samplerate = int(device_info['default_samplerate'])
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

    def process_command(self, utterance: str) -> None:
        """Interpret a recognised utterance and update the shared state.
        
        Args:
            utterance: The recognized speech text (lowercase)
            
        Parses the utterance to extract the command and updates the shared
        state accordingly. Commands can change lighting modes, trigger audio
        playback, or request AI-generated content.
        """
        m = self.command_pattern.match(utterance)
        if not m:
            return
        command = m.group(1).lower()
        rest = m.group(2) or ""
        # Handle colour commands
        if command in SUPPORTED_COLOURS:
            self.state.mode = command
            return
        # Disco and phase modes
        if command in ["disco", "phase"]:
            self.state.mode = command
            return
        # Speak: play the bundled MP3
        if command == "speak":
            self.state.audio_type = "speak"
            self.state.audio_event.set()
            return
        # Sing: play the configured song
        if command == "sing":
            self.state.audio_type = "sing"
            self.state.audio_event.set()
            return
        # Generate: use TTS to generate speech and play via VLC
        if command == "generate":
            self.state.text_to_speak = DEFAULT_GENERATE_TEXT
            self.state.audio_type = "generate"
            self.state.audio_event.set()
            return
        # Joke: fetch a joke from GreenPT API and speak it
        if command == "joke":
            self.state.audio_type = "joke"
            self.state.audio_event.set()
            return
        # Flatter: fetch flattery from GreenPT API and speak it
        if command == "flatter":
            self.state.audio_type = "flatter"
            self.state.audio_event.set()
            return
        # GB: display GB flag pattern
        if command == "gb":
            self.state.mode = "geebee"
            return


# -----------------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------------

def main() -> int:
    """Entry point for the offline voice‑controlled Christmas tree."""
    parser = argparse.ArgumentParser(
        description="Offline voice-controlled Christmas tree",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
TTS Engine Options:
  auto      - Auto-detect (prefer Piper if available, otherwise pyttsx3) [default]
  piper     - Use Piper TTS (requires PIPER_MODEL_PATH environment variable)
  pyttsx3   - Use pyttsx3 (espeak backend)

LLM Provider Options:
  greenpt   - Use GreenPT API (requires GREENPT_API_KEY environment variable) [default]
  ollama    - Use local Ollama (requires Ollama running: ollama serve)

Examples:
  %(prog)s                    # Auto-detect TTS engine, use GreenPT
  %(prog)s --tts-engine piper  # Force Piper TTS
  %(prog)s --tts-engine pyttsx3  # Force pyttsx3
  %(prog)s --llm-provider ollama  # Use local Ollama instead of GreenPT
        """
    )
    parser.add_argument(
        '--tts-engine',
        choices=['auto', 'piper', 'pyttsx3'],
        default='auto',
        help='TTS engine to use (default: auto)'
    )
    parser.add_argument(
        '--llm-provider',
        choices=['greenpt', 'ollama'],
        default='greenpt',
        help='LLM provider for joke and flattery commands (default: greenpt)'
    )
    args = parser.parse_args()

    # Convert 'auto' to None for AudioController
    tts_preference = None if args.tts_engine == 'auto' else args.tts_engine

    # Import the appropriate LLM provider module
    global get_joke, get_flattery
    if args.llm_provider == 'ollama':
        from ollama import get_joke, get_flattery, get_model
        llm_provider_name = "Ollama"
        llm_model_name = get_model()
    else:
        from greenpt import get_joke, get_flattery, get_model
        llm_provider_name = "GreenPT"
        llm_model_name = get_model()
    
    print("Starting offline voice‑controlled Christmas tree…")
    # Instantiate the hardware tree.  Set brightness to a reasonable default.
    tree = RGBXmasTree(brightness=0.1)
    # Create and start the worker threads
    led_thread = XmasTreeController(tree, STATE)
    audio_thread = AudioController(STATE, tts_preference=tts_preference)
    voice_thread = VoiceRecognizer(STATE)
    
    # Print configuration summary
    vosk_model_path = os.path.expanduser(os.path.expandvars(MODEL_PATH))
    # Try to determine the actual model name
    vosk_model_name = os.environ.get("VOSK_MODEL_NAME", None)  # Allow explicit override
    if not vosk_model_name:
        model_path_obj = Path(vosk_model_path)
        if model_path_obj.exists():
            # Check if parent directory looks like a model name (contains "vosk-model")
            parent = model_path_obj.parent
            if parent.name.startswith("vosk-model"):
                vosk_model_name = parent.name
            else:
                # Model is directly in the directory, try to find model name from zip files
                # or use the directory name
                project_dir = Path(__file__).parent
                # Look for zip files that might indicate the model name
                for zip_file in project_dir.glob("vosk-model-*.zip"):
                    # Extract model name from zip filename
                    potential_name = zip_file.stem  # filename without .zip
                    if potential_name.startswith("vosk-model"):
                        vosk_model_name = potential_name
                        break
                # If still not found, use directory name
                if not vosk_model_name:
                    vosk_model_name = os.path.basename(vosk_model_path)
    vlc_volume = int(os.environ.get("VLC_VOLUME", 75))
    print("=" * 60)
    print("Configuration Summary")
    print("=" * 60)
    print(f"Vosk Model: {vosk_model_name}")
    print(f"VLC Volume: {vlc_volume}%")
    print(f"TTS Engine: {audio_thread.tts_engine_name} | Voice/Model: {audio_thread.tts_voice_or_model}")
    print(f"LLM Provider: {llm_provider_name} | Model: {llm_model_name}")
    print("-" * 60)
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
