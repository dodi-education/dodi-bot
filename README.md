# DODI - Didactic Doing

A robot companion for kids, powered by Raspberry Pi and AI.

## Hardware Requirements

- **Raspberry Pi 4** (8GB RAM recommended)
- **Seeed Studio ReSpeaker 2-Mics Pi HAT**
  - 2x Microphones
  - APA102 RGB LEDs
  - User button
- **Speaker** (connected via JST 2.0 to ReSpeaker HAT)

## Software Requirements

- Raspberry Pi OS Lite (64-bit)
- Python 3.11+
- Internet connection (for cloud AI providers)

## Quick Start

### 1. Clone the Repository

```bash
cd ~
git clone https://github.com/yourusername/dodi.git
cd dodi
```

### 2. Run the Installation Script

**Interactive mode** (recommended for first-time setup):
```bash
sudo ./scripts/install.sh
```

The script will prompt you to:
1. Select your AI provider (Gemini, OpenAI, Grok, or skip)
2. Enter your API key (hidden input)

**Automated mode** (for scripting or re-installation):
```bash
# Fully automated
sudo ./scripts/install.sh --provider gemini --api-key YOUR_API_KEY

# Skip API setup (configure later)
sudo ./scripts/install.sh --provider skip

# Show all options
sudo ./scripts/install.sh --help
```

**Available parameters:**
| Parameter | Short | Description |
|-----------|-------|-------------|
| `--provider` | `-p` | AI provider: `gemini`, `openai`, `grok`, `skip` |
| `--api-key` | `-k` | API key for the selected provider |
| `--language` | `-l` | Language code (default: `en-US`) |
| `--skip-models` | | Skip downloading local AI models |
| `--help` | `-h` | Show help message |

The script will:
- Update system packages
- Install required system dependencies
- Install Seeed Voice Card audio driver (if not present)
- Create a Python virtual environment
- Install Python dependencies
- Configure `.env` with your API key
- Set the active provider in `settings.json`

No local AI models are downloaded — DODI uses a cloud live-conversation
provider (e.g. Gemini Live) for both speech recognition and speech synthesis.

**Note:** If the audio driver is installed, a reboot is required.

### 3. Configure Settings (Optional)

The install script configures the basics. For advanced customization, edit `config/settings.json`:

```bash
nano config/settings.json
```

Key settings:
- `streaming.active`: Live provider (set during install): `gemini`, `openai`, `grok`
- `streaming.<provider>.model` / `.voice`: Model and voice for the live conversation
- `language.code`: Set language (e.g., "en-US", "de-DE")
- `persona.active`: Choose persona ("default", "teacher", "storyteller")
- `conversation.timeout_seconds`: Idle seconds before DODI returns to DEAF mode

To change your API key later, edit the `.env` file:
```bash
nano .env
```

### 4. Run DODI

```bash
source venv/bin/activate
python src/main.py
```

## How DODI Works

DODI has two modes, and the **microphone is off by default** for privacy:

- **DEAF** (default at startup): the mic is fully off. The avatar wears
  headphones to show it can't hear.
- **NORMAL**: the mic is on and audio streams to/from the live AI provider for a
  natural, full-duplex conversation (the provider handles turn-taking).

**Double-tap the screen** to switch:
- DEAF → NORMAL: mic on, connect, start talking.
- NORMAL → DEAF: immediately disconnect and turn the mic off.

In NORMAL mode, after `conversation.timeout_seconds` of inactivity (no one
speaking), DODI automatically returns to DEAF.

## Project Structure

```
dodi/
├── config/
│   └── settings.json         # Main configuration
├── personas/
│   ├── default.txt            # Default personality
│   ├── teacher.txt            # Educational mode
│   └── storyteller.txt        # Creative storytelling mode
├── scripts/
│   └── install.sh             # Installation script
├── src/
│   ├── audio/                 # Microphone capture + playback
│   ├── conversation/          # Two-mode (DEAF/NORMAL) state + inactivity timer
│   ├── display/               # Round-screen avatar (pygame), double-tap input
│   ├── night_mode/            # Scheduled clock/alarm display
│   ├── streaming/             # Live conversation providers
│   │   ├── provider_base.py   # Abstract base class
│   │   ├── gemini_live.py     # Google Gemini Live
│   │   ├── openai_realtime.py # OpenAI Realtime
│   │   ├── grok_realtime.py   # xAI Grok
│   │   └── factory.py         # Provider factory
│   └── main.py                # Application entry point + control loop
├── venv/                      # Python virtual environment
├── .env                       # API keys (not in git)
├── .env.example               # API key template
└── requirements.txt           # Python dependencies
```

## AI Providers

DODI streams a live voice conversation to one of these providers. Configure the
active one under `streaming` in `settings.json`:

| Provider | API Key Variable | Notes |
|----------|------------------|-------|
| Gemini Live | `GEMINI_API_KEY` | Default; free tier available |
| OpenAI Realtime | `OPENAI_API_KEY` | Most widely used |
| Grok | `GROK_API_KEY` | xAI's model |

The provider handles speech recognition, the model, and speech synthesis in one
streaming session — there are no local STT/TTS models.

## Personas

Personas define DODI's personality and behavior. Create custom personas by adding `.txt` files to the `personas/` directory.

### Persona Format

```text
You are DODI, [description of personality].

PERSONALITY:
- Trait 1
- Trait 2

GUIDELINES:
- Rule 1
- Rule 2

VOICE STYLE:
- Style notes
```

### Available Personas

- `default`: Friendly companion
- `teacher`: Educational focus
- `storyteller`: Creative narratives

## Configuration Reference

### settings.json

```json
{
  "language": {
    "code": "de",           // Language code
    "auto_detect": false
  },
  "conversation": {
    "timeout_seconds": 10.0  // Idle seconds in NORMAL before returning to DEAF
  },
  "persona": {
    "active": "default_de", // Active persona name
    "directory": "personas/"
  },
  "audio": {
    "volume": 50,           // Output volume (0-100)
    "input_device": "seeed-2mic-voicecard",
    "sample_rate": 16000
  },
  "streaming": {
    "enabled": true,
    "active": "gemini",     // Live provider: gemini, openai, grok
    "gemini": { "model": "gemini-3.1-flash-live-preview", "voice": "Leda" },
    "openai": { "model": "gpt-realtime", "voice": "alloy" },
    "grok":   { "model": "grok-4-voice", "voice": "Ara" }
  }
}
```

## Troubleshooting

### Audio not working

1. Check if Seeed driver is installed:
   ```bash
   arecord -l
   ```
   You should see "seeed" in the output.

2. Reboot if recently installed:
   ```bash
   sudo reboot
   ```

3. Test recording:
   ```bash
   arecord -D plughw:1,0 -f S16_LE -r 16000 -d 5 test.wav
   ```

4. Test playback:
   ```bash
   aplay -D plughw:1,0 test.wav
   ```

### API connection issues

1. Verify your API key in `.env`
2. Check internet connection
3. Test with:
   ```bash
   source venv/bin/activate
   python -c "import sys; sys.path.insert(0, 'src'); from streaming.factory import is_provider_available; print('gemini key set:', is_provider_available('gemini'))"
   ```

### Import errors

Run the install script again:
```bash
sudo ./scripts/install.sh
```

## Todo

- Fetch weather forecast and add as context to dodi so you can ask (how will be the weather today / tomorrow), open-meteo api
- Update install.sh (make it idempodent)
- Install should add to auto-start


## License

MIT License - See LICENSE file for details.
