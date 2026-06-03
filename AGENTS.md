## PROJECT GOALS:
- Create an AI companion bot for kids named Dodi
- The bot must run smoothly on a Raspberry Pi 4 8GB with ReSpeaker 2-Mics Pi HAT
- Dodi should respond to questions as fast as possible
- Response voice should be as smooth as possible and have a friendly tone
- The bot should be easily installable with an install.sh script which should support idempotent (repeated) calls
- The hardware should be embeddable into a plush cover (plushie) without overheating

## SPECIFIC FEATURES:
- The bot is privacy focused, so it has two modes (deaf/normal).
- It starts in deaf mode by default: the microphone is fully off and nothing is sent to AI providers. The avatar wears headphones to show it can't hear.
- A double-tap on the screen toggles modes. From deaf, a double-tap switches to normal mode: the mic turns on and the bot streams a native, full-duplex live conversation to a cloud AI provider (e.g. Gemini Live) — the provider handles turn-taking.
- In normal mode, a double-tap switches back to deaf mode immediately (disconnect + mic off).
- In normal mode, after a configurable inactivity timeout (`conversation.timeout_seconds`) with no one speaking, the bot returns to deaf mode automatically.
- There is no wake word and no offline speech-to-text / text-to-speech: speech recognition and synthesis happen inside the cloud live-conversation session.

## ALARM:
- Alarms are decoupled from night mode (night mode is now only a scheduled clock/blank display). Alarm config lives in the top-level `alarm` block of settings.json: a per-day weekly `schedule` plus a `one_time_alarm` override (each `{active, time, audio}`). A one-time alarm overrides the next ring, then auto-deactivates.
- A standalone alarm engine (`src/alarm/`) fires alarms at their time (looped audio + lit clock), dismissed by a screen tap.
- A **two-finger swipe-down** on the avatar face opens the alarm settings screen (Weekly/One-time tabs, day strip, circular drag time-dial, sound picker, Active switch; day/night theme). Edits persist back to settings.json. The clock screen shows the next upcoming alarm (icon + day + time) at the bottom.