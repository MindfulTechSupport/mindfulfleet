# Changelog

## 0.2.6 (2026-11-10)

- Electricity price takes three decimal places, and small daily costs show to a tenth of a cent.
- Fixed: on narrow cards, long values like pool names pushed numbers off the right edge. Bars now wrap above their numbers when space is tight, and the cores show 2 by 2.
- − and + buttons on each slider. Each click moves 1 pixel or 1%. Holding a button keeps it moving, faster the longer it's held.
- The Pool row says whether the link to the pool is encrypted, shows the rig ID, and warns when two computers share a rig ID, since the pool can't tell them apart.

## 0.2.5 (2026-11-10)

- Fixed: the dashboard installer left out a file added in 0.2.2, so an updated dashboard couldn't start. A test now checks that every file the dashboard needs gets installed.

## 0.2.4 (2026-11-10)

- Mining: hashrate against the best 15-minute rate seen, the 15-minute rate, cores mining, shares accepted, pool, and hashes per watt, read from XMRig's built-in web API on each computer.
- Fleet hashrate and hashes per watt under the headline, and hashrate in the averages table.
- `sudo mindful-fleet xmrig` turns on XMRig's API for that computer only, in read-only mode. It shows the change first, asks, and keeps a backup.

## 0.2.3 (2026-11-10)

- Power use in watts on Pi 5 boards, read from the board's power chip, with kWh a day and an optional cost per day.
- Fleet power total under the headline.
- An electricity price setting, shared through the hub.
- Averages of power use and of the share of time a computer spent slowed down.
- `mindful-fleet every hourly|6h|daily|weekly` sets how often each computer checks for updates.
- Separate card width, text size, and card height sliders. With a set height, each card scrolls, and keeps its place as numbers refresh.

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
