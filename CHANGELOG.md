# Changelog

## 0.1.0 (2026-11-02)

First version.

- Reporter sends temperature, throttling, fan, per-core CPU, memory, storage, and uptime every 15 seconds.
- Hub and local dashboard show one card per computer, colored by health, with plain-English warnings and a temperature trend.
- Computers that stop reporting show as offline, including after the hub restarts.
- Demo mode, service installers, and tests.
- Tested on a five-unit Raspberry Pi 5 fleet running Raspberry Pi OS Lite. See "Known issues" in the README.
