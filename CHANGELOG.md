# Changelog

## 0.2.2 (2026-11-10)

- Averages over 5 minutes, 15 minutes, 30 minutes, 1 hour, and 24 hours for each core, CPU, clock speed, load, temperature, fan, memory, voltage, network, storage speed, and Wi-Fi. The hub keeps a day of history in about 350 small buckets per computer.
- New readings: Wi-Fi signal strength, storage read and write speed, and the busiest program with how many cores it uses.
- Cards say when a newer version is available. The hub checks GitHub every 6 hours, and `--no-update-check` turns that off.
- Card order, groups, and row choices are saved on the hub, so every browser sees the same layout. Card size stays per browser.
- The hub saves its memory every 5 minutes instead of every 30 seconds, to spare SD cards.

## 0.2.1 (2026-11-10)

- Each card's top shows the board model and memory size, such as "Raspberry Pi 5, 8 GB".
- Numbers and limits beside every graphic: memory and storage used of total, cores in use, fan's fastest speed seen.
- New readings: clock speed now and maximum, load, swap, network in and out, core voltage, and system version.
- Each core shows its load now, its 30-minute average, and its clock speed.
- Choose which rows the cards show, saved in your browser.
- Automatic daily updates from the release branch, with commands to hold, skip, or resume them on each computer.

## 0.2.0 (2026-11-03)

- Card size slider, your own card order, and groups, saved in your browser.
- Shows how many cores are in use, with each core's load now and its 30-minute average.
- Graphics with numbers for CPU, fan effort, and voltage. Pi 5 boards report voltage.
- Fixed a false warning on boards that report 0 RPM with no fan attached.
- Calmer temperature trend line.
- Cards sort in number order, including spelled-out names like BrainOne and BrainTwo.
- One-line install and update from GitHub. The token prompt now shows what you paste.

## 0.1.0 (2026-11-02)

First version.

- Reporter sends temperature, throttling, fan, per-core CPU, memory, storage, and uptime every 15 seconds.
- Hub and local dashboard show one card per computer, colored by health, with plain-English warnings and a temperature trend.
- Computers that stop reporting show as offline, including after the hub restarts.
- Demo mode, service installers, and tests.
- Tested on a five-unit Raspberry Pi 5 fleet running Raspberry Pi OS Lite. See "Known issues" in the README.
