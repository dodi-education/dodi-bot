## PROJECT GOALS:
- Create an AI companion bot for kids named Dodi
- The bot must run smoothly on a Raspberry Pi 4 8GB with ReSpeaker 2-Mics Pi HAT
- Dodi should respond to questions as fast as possible
- Response voice should be as smooth as possible and have a friendly tone
- The bot should be easily installable with an install.sh script which should support idempotent (repeated) calls
- The hardware should be embeddable into a plush cover (plushie) without overheating

## SPECIFIC FEATURES:
- The bot should be privacy focused, so it has two modes (passive/active).
- In passive mode, local voice detection listens for the wake word but never forwards voice or text to ai providers.
- Wake word detection switches the bot from passive to active
- Once active the bot transmits text or voice (depending on configuration) to ai providers so the user can actively communicate with the ai
- The bot can be switched back with a stop function which can be invoked by a stop word (if configured) or a push of a button (e.g. space)
- Once AI has finished their response, start a stop timer after which the mode gets switched to passive again automatically. The timeout should be configurable too.