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
- Download local AI models (Vosk STT, Piper TTS)
- Configure `.env` with your API key
- Set the active provider in `settings.json`

**Note:** If the audio driver is installed, a reboot is required.

### 3. Configure Settings (Optional)

The install script configures the basics. For advanced customization, edit `config/settings.json`:

```bash
nano config/settings.json
```

Key settings:
- `ai_provider.active`: AI provider (set during install)
- `language.code`: Set language (e.g., "en-US", "de-DE")
- `persona.active`: Choose persona ("default", "teacher", "storyteller")
- `wake_word.phrase`: Change wake phrase (default: "hey dodi")

To change your API key later, edit the `.env` file:
```bash
nano .env
```

### 4. Run DODI

```bash
source venv/bin/activate
python src/main.py
```

## Project Structure

```
dodi/
├── config/
│   └── settings.json         # Main configuration
├── models/
│   ├── stt/                   # Speech-to-Text models (Vosk)
│   └── tts/                   # Text-to-Speech models (Piper)
├── personas/
│   ├── default.txt            # Default personality
│   ├── teacher.txt            # Educational mode
│   └── storyteller.txt        # Creative storytelling mode
├── scripts/
│   └── install.sh             # Installation script
├── src/
│   ├── ai/                    # AI provider implementations
│   │   ├── provider_base.py   # Abstract base class
│   │   ├── gemini_provider.py # Google Gemini
│   │   ├── openai_provider.py # OpenAI ChatGPT
│   │   ├── grok_provider.py   # xAI Grok
│   │   └── factory.py         # Provider factory
│   └── main.py                # Application entry point
├── venv/                      # Python virtual environment
├── .env                       # API keys (not in git)
├── .env.example               # API key template
└── requirements.txt           # Python dependencies
```

## AI Providers

DODI supports multiple AI providers. Configure in `settings.json`:

| Provider | Model | API Key Variable | Notes |
|----------|-------|------------------|-------|
| Gemini | gemini-2.0-flash | `GEMINI_API_KEY` | Free tier available |
| OpenAI | gpt-4o-mini | `OPENAI_API_KEY` | Most widely used |
| Grok | grok-2 | `GROK_API_KEY` | xAI's model |

## Local Models

DODI uses local models for voice processing (no cloud required):

- **Speech-to-Text:** Vosk (lightweight, offline)
- **Text-to-Speech:** Piper (natural voices, fast)

Models are downloaded automatically by the install script.

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
    "code": "en-US",        // Language code
    "auto_detect": false    // Auto-detect language (saves bandwidth if false)
  },
  "ai_provider": {
    "active": "gemini",     // Active provider: gemini, openai, grok
    "gemini": { ... },
    "openai": { ... },
    "grok": { ... }
  },
  "local_models": {
    "stt": {
      "engine": "vosk",
      "model_path": "models/stt/vosk-model-small-en-us-0.15"
    },
    "tts": {
      "engine": "piper",
      "model_path": "models/tts/en_US-lessac-medium.onnx"
    }
  },
  "persona": {
    "active": "default",    // Active persona name
    "directory": "personas/"
  },
  "audio": {
    "volume": 80,           // Output volume (0-100)
    "sample_rate": 16000    // Audio sample rate
  },
  "wake_word": {
    "enabled": true,
    "phrase": "hey dodi"
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
   python -c "from src.ai import create_provider; print('OK')"
   ```

### Import errors

Run the install script again:
```bash
sudo ./scripts/install.sh
```

## License

MIT License - See LICENSE file for details.
