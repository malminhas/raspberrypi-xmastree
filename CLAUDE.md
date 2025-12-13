# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Raspberry Pi Christmas tree controller with three implementations:
- `my-tree.py`: Simple disco mode LED animation
- `my-voice-tree.py`: AWS-based voice control (requires internet, AWS credentials)
- `offline_voice_tree.py`: Fully offline voice control using Vosk and pyttsx3 (recommended)

The offline version is the primary focus and has full architectural documentation in ARCHITECTURE.md.

## Development Environment Setup

### Virtual Environment (Required)

Must use `--system-site-packages` to access GPIO libraries:

```bash
python3 -m venv --system-site-packages ~/.virtualenvs/xmastree
source ~/.virtualenvs/xmastree/bin/activate
pip install -r requirements.txt
```

### Hardware Dependencies

On Raspberry Pi 4/5, install GPIO support first:
```bash
sudo apt install python3-gpiozero python3-lgpio
```

### Offline Voice Tree Additional Setup

```bash
# Install system dependencies
sudo apt install portaudio19-dev espeak-ng vlc

# Install Python packages
pip install vosk pyttsx3 python-vlc sounddevice

# Download Vosk model (required for speech recognition)
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip
mv vosk-model-small-en-us-0.15 model
```

### Cloud Voice Tree Setup (Optional)

Create `local.env` with AWS credentials:
```bash
export AWS_ACCESS_KEY_ID=your_key_here
export AWS_SECRET_ACCESS_KEY=your_secret_here
export AWS_DEFAULT_REGION=us-west-2
```

## Running the Applications

### Simple Disco Tree
```bash
python my-tree.py
```

### Offline Voice-Controlled Tree (Recommended)
```bash
python offline_voice_tree.py
```

### Cloud Voice-Controlled Tree
```bash
source local.env
python my-voice-tree.py
```

All scripts run indefinitely until CTRL-C.

## Voice Commands

All voice commands start with "christmas tree" followed by:

**Colors**: red, green, blue, yellow, orange, purple, white, pink, brown, black
**Modes**: disco (random color cycling), phase (synchronized hue cycling)
**Audio**: speak (play speech.mp3), generate (TTS), sing (play music file)

Example: "christmas tree red" or "christmas tree disco"

## Architecture Notes

### Thread Architecture (offline_voice_tree.py)

Three cooperative threads sharing a `State` object:

1. **VoiceRecognizer** (offline_voice_tree.py:416-520)
   - Captures audio via sounddevice
   - Uses Vosk for speech recognition with constrained grammar
   - Updates shared state on command recognition
   - Auto-detects ReSpeaker mic (lines 47-70), falls back to default

2. **XmasTreeController** (offline_voice_tree.py:157-278)
   - Polls `state.mode` every 50ms
   - Drives LED animations (disco, phase, solid colors)
   - 25 LEDs split into 3 groups (lines 167), star at index 3 (line 170)
   - Reinitializes colors on mode transitions for visual feedback

3. **AudioController** (offline_voice_tree.py:284-410)
   - Waits on `state.audio_event` signal
   - Switches to idle mode (LEDs off) during playback
   - Plays MP3 via VLC with ALSA `plughw:X,0` device
   - Generates TTS using pyttsx3 → temp WAV → VLC playback
   - Restores previous LED mode after audio completes

### Hardware Driver (tree.py)

`RGBXmasTree` class inherits from `SPIDevice`:
- Wraps 25 APA102 RGB LEDs + 1 white star
- SPI protocol: MOSI=GPIO12, CLK=GPIO25
- Brightness encoded as 5-bit value (0-31)
- Data format: `[0]*4 + [brightness, B, G, R]*25 + [0]*5`

### State Synchronization

Shared `State` object (offline_voice_tree.py:129-148):
- `mode`: Current LED mode (str, atomic writes)
- `last_mode`: Backup before audio playback
- `audio_event`: threading.Event for signaling
- `stop_event`: Graceful shutdown signal

No explicit locks needed due to Python GIL and atomic string assignments.

### Vosk Grammar Constraints

Recognition limited to predefined phrases (line 435):
```python
grammar_phrases = [f"christmas tree {cmd}" for cmd in SUPPORTED_COLOURS + SUPPORTED_COMMANDS]
```
This improves accuracy with small models but prevents capturing arbitrary text after commands.

### ALSA Audio Device Selection

ReSpeaker detection extracts ALSA card number via regex (lines 50-61):
```python
match = re.search(r'hw:(\d+),0', dev['name'])
```
Uses `plughw:X,0` instead of `hw:X,0` for automatic sample rate conversion (see play_mp3 docstring, lines 297-315).

## Key Configuration Constants

Located at top of offline_voice_tree.py (lines 91-124):

- `MODEL_PATH`: Vosk model directory (env: `VOSK_MODEL_PATH`)
- `SUPPORTED_COLOURS`: 10 color names
- `SUPPORTED_COMMANDS`: disco, phase, speak, generate, sing
- `SPEECH_MP3_PATH`: Audio file for "speak" command
- `SING_MP3_PATH`: Music file for "sing" command
- `DEFAULT_GENERATE_TEXT`: TTS text (grammar prevents capturing custom text)

## Testing Audio Devices

```bash
# List available audio devices
python3 -c "import sounddevice as sd; print(sd.query_devices())"

# Test Vosk model
python3 -c "from vosk import Model; m = Model('model'); print('Model loaded')"

# Test TTS
python3 -c "import pyttsx3; e = pyttsx3.init(); e.say('test'); e.runAndWait()"
```

## Graceful Shutdown Pattern

All threads check `stop_event` with timeouts:
```python
while not self.state.stop_event.is_set():
    data = self.q.get(timeout=0.1)  # Allows periodic checking
```

Main thread signals shutdown on KeyboardInterrupt:
```python
STATE.stop_event.set()
led_thread.join(timeout=1.0)
tree.close()  # Prevents GPIO resource leaks
```

## Boot Configuration (Production)

Add to `/etc/rc.local`:
```bash
. /home/pi/.virtualenvs/xmastree/bin/activate
python /home/pi/Desktop/CODE/raspberrypi-xmastree/offline_voice_tree.py
```

Headless pulseaudio may need configuration (see README troubleshooting).

## File Roles

- `offline_voice_tree.py`: Main application (offline voice control)
- `my-voice-tree.py`: AWS cloud-based version (expensive)
- `my-tree.py`: Simple demo without voice control
- `tree.py`: Hardware driver (RGBXmasTree class)
- `requirements.txt`: Base Python dependencies (expand for offline/cloud versions)
- `speech.mp3`: Audio file played by "speak" command
- `08-I-Wish-it-Could-be-Christmas-Everyday.mp3`: Song for "sing" command
- `model/`: Vosk speech recognition model (not in git)
- `local.env`: AWS credentials (not in git)
- `ARCHITECTURE.md`: Comprehensive C4 model documentation
- `README.md`: Installation instructions and feature comparison

## Important Implementation Details

### LED Mode Transitions
Mode changes explicitly reinitialize LED colors (lines 213-237) to provide clear visual feedback. Without this, hue cycling appears "stuck" when returning to disco mode.

### Audio Playback Volume
VLC volume set to 50% (line 330). Adjust in `play_mp3()` method if needed.

### TTS Rate Adjustment
Speech rate reduced by 25 (line 294) for clarity with espeak-ng.

### Device Auto-Detection
ReSpeaker detection is case-insensitive (line 55) and extracts ALSA card number. Missing ReSpeaker triggers fallback to default device with printed warning.

### Temporary File Cleanup
TTS generation creates temp WAV files (line 366) that are deleted after playback (line 378). Failure to cleanup is silently ignored.

## Common Issues

**"ReSpeaker microphone not found"**: Script continues with default input device. Check `lsusb` or `usb-devices`.

**"Vosk model not found"**: Download model and extract to `./model/` directory or set `VOSK_MODEL_PATH`.

**GPIO errors on exit**: Normal if tree.close() called multiple times. Wrapped in try/except.

**VLC "no supported sample format"**: Fixed by using `plughw:X,0` instead of `hw:X,0` (automatic conversion).

**Commands not recognized**: Check microphone levels and speak clearly. Grammar constraints require exact "christmas tree <command>" format.
