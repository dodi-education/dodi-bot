# DODI - Didactic Doing

A voice-first robot companion for kids, powered by Raspberry Pi and live AI.

DODI runs a natural, full-duplex voice conversation with a cloud AI provider
(Gemini Live, OpenAI Realtime, or xAI Grok) and shows an animated avatar on a
round touchscreen. Beyond chatting, it doubles as a bedside device: a swipe-able
info screen (time, weather, CPU temperature), a scheduled night-mode clock, and
a weekly/one-time alarm clock — all configured from on-screen touch menus, with
the microphone **off by default** for privacy.

## Features

- **Live voice conversation** — full-duplex streaming to Gemini / OpenAI / Grok;
  the provider handles speech recognition, the model, and speech synthesis.
- **Privacy-first** — starts in DEAF mode with the mic fully off; double-tap to
  talk, auto-returns to DEAF after a configurable idle timeout.
- **Animated avatar** on an 800×800 round DSI touch display (blink / smile /
  listen / laugh).
- **Info screen** — date & time, a live CPU-temperature gauge, and a 3-day
  weather forecast (Open-Meteo, no API key).
- **Weather-aware AI** — the current forecast is injected into the AI's system
  prompt, so DODI can talk about today's weather.
- **Night mode** — a scheduled clock (or blank screen) during configured hours
  that also silences the mic.
- **Alarm clock** — weekly + one-time alarms with looped audio and an optional
  ascending-volume ramp; dismiss with a tap.
- **On-device settings** — touch menus for volume, Wi-Fi, location, display
  power, and the night/alarm schedules. No keyboard needed.
- **Plug'n'play** — installs as a systemd service that auto-starts on boot.

## Hardware Requirements

- **Raspberry Pi 4** (8GB RAM recommended)
- **Seeed Studio ReSpeaker 2-Mics Pi HAT** (WM8960 codec)
  - 2× microphones
  - Onboard speaker amplifier (hardware-muted to suppress pops)
  - *(The HAT's APA102 LEDs and user button are not used by DODI — all
    interaction is via the touchscreen.)*
- **Waveshare 3.4" DSI Round Touch Display** (800×800, capacitive multi-touch)
- **Speaker** (connected via JST 2.0 to the ReSpeaker HAT)

## Software Requirements

- Raspberry Pi OS Lite (64-bit)
- Python 3.11+
- NetworkManager (`nmcli`) — used by the on-screen Wi-Fi setup (default on
  current Pi OS)
- An internet connection — **required**; DODI uses cloud AI providers and there
  are no local speech models

## Quick Start

### 1. Clone the Repository

```bash
cd ~
git clone https://github.com/birkeal/dodi-bot.git
cd dodi-bot
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
| `--help` | `-h` | Show help message |

The script will:
- Update system packages and install dependencies
- Install the Seeed Voice Card audio driver (WM8960) and add the `dtoverlay` to
  `config.txt` (if not already present)
- Create a Python virtual environment and install Python dependencies
- Configure `.env` with your API key and set the active provider in
  `settings.json`
- Install and enable the `dodi.service` systemd unit (auto-start on boot)

The display (Waveshare DSI round panel) is driven by the `vc4-kms-v3d` +
`vc4-kms-dsi-waveshare-panel` overlays, which are expected to be present on the
panel's standard Pi OS image.

**Note:** If the audio driver is installed, a reboot is required.

### 3. Configure Settings (Optional)

Most things can be changed from the on-screen **System Settings** (two-finger
swipe up). For advanced customization, edit `config/settings.json` directly:

```bash
nano config/settings.json
```

Key settings:
- `streaming.active`: live provider (`gemini`, `openai`, `grok`)
- `streaming.<provider>.model` / `.voice`: model and voice for the conversation
- `language.code`: language (e.g., `"en"`, `"de"`)
- `persona.active`: personality prompt (`default`, `default_de`, `teacher`,
  `storyteller`)
- `conversation.timeout_seconds`: idle seconds before DODI returns to DEAF
- `weather.lat` / `weather.lon` / `weather.location_name`: forecast location
- `night_mode.schedule` and `alarm.schedule`: per-day windows and alarm times

To change your API key later, edit the `.env` file:
```bash
nano .env
```

### 4. Run DODI

The installer sets up a `dodi.service` systemd unit and enables it, so **DODI
starts automatically on every boot** — just power on the device. SSH stays
available in the background as usual.

Control the service:

```bash
sudo systemctl start dodi      # start now (also starts automatically on boot)
sudo systemctl stop dodi       # stop
sudo systemctl status dodi     # check status
journalctl -u dodi -f          # follow logs
sudo systemctl disable dodi    # turn off auto-start on boot
```

**Running manually (development):** stop the service first so it doesn't hold
the display, then launch directly:

```bash
sudo systemctl stop dodi
source venv/bin/activate
python src/main.py
```

## Using DODI

### Modes (talking)

The **microphone is off by default** for privacy:

- **DEAF** (default at startup): the mic is fully off. The avatar wears
  headphones to show it can't hear.
- **NORMAL**: the mic is on and audio streams to/from the live AI provider for a
  natural, full-duplex conversation (the provider handles turn-taking).

**Double-tap the screen** to toggle. In NORMAL mode, after
`conversation.timeout_seconds` of inactivity (no one speaking), DODI
automatically returns to DEAF. Double-tapping again drops back to DEAF
immediately and disconnects.

### Screens & gestures

DODI is touch-only — there is no physical button. The display shows one of four
views (avatar, info, night clock, blank), navigated by gestures:

| Gesture | Where | Action |
|---------|-------|--------|
| **Double-tap** | Avatar | Start / stop talking (DEAF ↔ NORMAL) |
| **Single tap** | Avatar | DODI laughs |
| **Swipe left / right** | Avatar ↔ Info | Show / hide the info screen |
| **Two-finger swipe up** | Any screen | Open **System Settings** |
| **Two-finger swipe down** | Any screen | Open **Alarm Settings** |
| **Tap** | Alarm ringing / night clock | Dismiss the alarm |
| **Tap** | Dark / parked screen | Wake the display |

### Info screen

Swipe left from the avatar to see the info screen: a large date & time tile, a
live **CPU-temperature gauge** (averaged each minute), and a **3-day weather
forecast** with condition icons and high/low temperatures. The conversation
keeps running while it's shown. Weather appears once a location is set and the
internet is reachable; otherwise a tap routes you to the relevant System
Settings section.

### Night mode

During the windows defined in `night_mode.schedule`, DODI switches the display
to a dim **clock** (or a blank screen, per `night_mode.display.mode`) and
silences the microphone. The clock also shows the next upcoming alarm. It
restores the avatar and resumes normally when the window ends.

### Alarms

Set weekly alarms (per weekday) or a one-time alarm from **Alarm Settings**
(two-finger swipe down). When an alarm fires it takes over the screen with the
clock, turns the display to full brightness, and loops the chosen audio
(`assets/audio/`). Volume is independent of the master volume and can ramp up
gradually (`alarm.ascending_volume`). **Tap the screen to dismiss.** One-time
alarms deactivate themselves after ringing.

### System settings

Two-finger swipe up to open System Settings — four sections:

- **General** — master volume.
- **Internet** — scan and join Wi-Fi networks (via `nmcli`; credentials are
  stored by NetworkManager, not by DODI) and set your location for weather.
- **Power** — idle timeouts that turn the display off (day vs. night) and an
  adaptive-FPS toggle.
- **Night** — enable night mode, set the brightness, and edit the per-day
  schedule.

Changes are written to `settings.json` and most take effect immediately.

## AI Providers

DODI streams a live voice conversation to one of these providers. Configure the
active one under `streaming` in `settings.json`:

| Provider | API Key Variable | Default model / voice | Notes |
|----------|------------------|-----------------------|-------|
| Gemini Live | `GEMINI_API_KEY` | `gemini-3.1-flash-live-preview` / `Leda` | Default; free tier available |
| OpenAI Realtime | `OPENAI_API_KEY` | `gpt-realtime` / `alloy` | Most widely used |
| Grok | `GROK_API_KEY` | `grok-4-voice` / `Ara` | xAI (OpenAI-compatible API) |

The provider handles speech recognition, the model, and speech synthesis in one
streaming session — there are no local STT/TTS models. The active forecast is
appended to the system prompt so DODI is weather-aware.

## Personas

Personas are plain-text system prompts in `personas/` that define DODI's name,
personality, and behavior. Set the active one with `persona.active` in
`settings.json` (the filename without `.txt`).

| Persona | Description |
|---------|-------------|
| `default` | Friendly companion (English) |
| `default_de` | Friendly companion (German) |
| `teacher` | Educational focus |
| `storyteller` | Creative narratives |

Create your own by adding a `.txt` file to `personas/`:

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

## Configuration Reference

`config/settings.json` is the single source of truth (loaded/saved atomically and
shared across threads). Abbreviated schema:

```jsonc
{
  "language": { "code": "de", "auto_detect": false },
  "conversation": { "timeout_seconds": 30.0 },   // idle secs in NORMAL → DEAF
  "persona": { "active": "default_de", "directory": "personas/" },

  "audio": {
    "volume": 30,                                  // master volume (0–100)
    "input_device": "seeed-2mic-voicecard",
    "sample_rate": 16000
  },

  "streaming": {
    "enabled": true,
    "active": "gemini",                            // gemini | openai | grok
    "gemini": { "model": "gemini-3.1-flash-live-preview", "voice": "Leda" },
    "openai": { "model": "gpt-realtime",            "voice": "alloy" },
    "grok":   { "model": "grok-4-voice",            "voice": "Ara" }
  },

  "weather": {                                     // set via Settings → Internet
    "location_name": "Klosterneuburg",
    "country_code": "AT",
    "lat": 48.30521, "lon": 16.32522
  },

  "power_options": {
    "turn_off_display": 60,                        // idle secs → screen off (day)
    "turn_off_display_night_mode": 3,              // idle secs → screen off (night)
    "adaptive_fps": true
  },

  "night_mode": {
    "enabled": true,
    "deactivate_sst": true,                        // silence mic during night mode
    "display": { "mode": "time", "brightness": 10 }, // "time" (clock) or "off" (blank)
    "schedule": {                                  // per-day list of "HH:MM-HH:MM"
      "Mon": [ { "timespan": "00:00-12:00" }, { "timespan": "22:00-24:00" } ]
      // ... Tue–Sun
    }
  },

  "alarm": {
    "volume": 25,                                  // independent of master volume
    "ascending_volume": { "active": true, "start_volume": 5, "duration": 20 },
    "stop_alarm_touch": true,
    "one_time_alarm": { "active": false, "time": "15:05", "audio": "morning.mp3" },
    "schedule": {                                  // per-day alarm
      "Mon": { "active": true, "time": "06:00", "audio": "morning.mp3" }
      // ... Tue–Sun
    }
  }
}
```

## Project Structure

```
dodi-bot/
├── config/
│   └── settings.json          # Single JSON config (shared across threads)
├── personas/                  # Personality system prompts (.txt)
│   ├── default.txt  default_de.txt  teacher.txt  storyteller.txt
├── assets/
│   ├── audio/morning.mp3      # Default alarm sound
│   ├── fonts/                 # Nunito TTFs (UI font)
│   └── icons/tabler/          # Vendored Tabler SVG icons (UI)
├── scripts/
│   └── install.sh             # Raspberry Pi setup + autostart service
├── src/
│   ├── main.py                # Entry point + DEAF/NORMAL control loop
│   ├── settings_store.py      # Atomic, thread-safe settings I/O
│   ├── alarm/                 # Standalone alarm engine (schedule + playback)
│   ├── audio/                 # Mic capture, playback, WM8960 mute (pop-free)
│   ├── conversation/          # DEAF/NORMAL state machine + inactivity timer
│   ├── display/               # Pygame avatar + info / settings / alarm screens
│   │   ├── avatar.py  info_screen.py  system_settings_screen.py
│   │   ├── settings_screen.py  detect.py  icons.py  theme.py  widgets.py
│   ├── network/               # weather.py (Open-Meteo), geocode.py, wifi.py (nmcli)
│   ├── night_mode/            # Scheduled clock/blank display + backlight control
│   ├── sensors/               # cpu_temp.py (thermal-zone reader for info screen)
│   └── streaming/             # Live providers
│       ├── provider_base.py  factory.py
│       ├── gemini_live.py  openai_realtime.py  grok_realtime.py
├── tests/                     # Pytest suite (modes, schedules, screens, parsers)
├── venv/                      # Python virtual environment (gitignored)
├── .env                       # API keys (not in git)
├── .env.example               # API key template
└── requirements.txt           # Python dependencies
```

## Development

Run the test suite (headless — pygame uses a dummy video driver, network parsers
use canned data):

```bash
source venv/bin/activate
pytest
```

Run the app manually with debug logging (stop the service first so it doesn't
hold the display):

```bash
sudo systemctl stop dodi
python src/main.py --debug
```

## Troubleshooting

### Audio not working

1. Check if the Seeed driver is installed:
   ```bash
   arecord -l
   ```
   You should see "seeed" in the output.
2. Reboot if the driver was just installed:
   ```bash
   sudo reboot
   ```
3. Test recording and playback:
   ```bash
   arecord -D plughw:1,0 -f S16_LE -r 16000 -d 5 test.wav
   aplay -D plughw:1,0 test.wav
   ```

### Display / touch not working

1. Confirm the round panel is detected (800×800):
   ```bash
   cat /sys/class/graphics/fb0/virtual_size
   ```
2. Check the panel overlay is present in `/boot/firmware/config.txt`
   (`vc4-kms-v3d` and `vc4-kms-dsi-waveshare-panel`).
3. If the avatar doesn't appear after boot, inspect the service logs:
   ```bash
   journalctl -u dodi -b
   ```

### API connection issues

1. Verify your API key in `.env`.
2. Check the internet connection (and Wi-Fi via **Settings → Internet**).
3. Test the provider key:
   ```bash
   source venv/bin/activate
   python -c "import sys; sys.path.insert(0, 'src'); from streaming.factory import is_provider_available; print('gemini key set:', is_provider_available('gemini'))"
   ```

### Service won't start

```bash
systemctl status dodi
journalctl -u dodi -b      # full logs from this boot
```

A common cause is the display being held by something else — make sure no manual
`python src/main.py` is running.

## License

MIT License — see the LICENSE file for details.
