# Changelog

## 0.2.12 (2026-11-11)

- Find my Pis: Settings → Find my Pis → Look now lists the Raspberry Pis on the hub's network (the 254 addresses around the hub), marks which are already on the dashboard, and shows whether SSH is on. Read-only, only when you press the button, and only once the dashboard has a password.
- `sudo mindful-fleet tidy` frees up space on a computer: it shows what it would clear (downloaded update files, and the system log beyond its newest 100 MB), asks first, and shows the space freed. It can also keep the log under 100 MB from now on, for less SD card wear; `tidy undo` removes that limit.
- `sudo mindful-fleet wifi-watchdog on` reconnects Wi-Fi by itself, including when the connection drops altogether: after about 6 minutes without the router it restarts Wi-Fi, and after 3 restarts that don't help it reboots, at most once an hour and less often while the router stays away (up to once a day). It turns on only when the router answers. Off by default.
- The Pi tools are installed on hub-only computers too.
- The "Remove it" steps now also remove the update timer, the Wi-Fi watchdog, the log limit and the `mindful-fleet` command, and carry on if a part was never installed.

## 0.2.11 (2026-11-11)

- Dashboard password, the first step toward remote control. Set it on the hub with `sudo mindful-fleet password`. The dashboard then asks for it; each browser stays signed in for 30 days. Change it or sign out under Settings → Password and sign out.
- Wrong guesses slow down: a few free tries, then a wait that doubles each time, up to 5 minutes.
- The hub keeps only a salted hash of the password. Changing it signs out every other browser. `sudo mindful-fleet password off` turns it off and keeps the old one so it can be put back.
- Until a password is set, the dashboard works as before and shows a reminder.
- The hub refuses dashboard changes sent from other websites.
- Controls size now goes down to 50% (was 60%).

## 0.2.10 (2026-11-10)

- New name: MindfulFleet. The GitHub repository is now MindfulTechSupport/mindfulfleet; old links still work. The `mindful-fleet` command, its folders and its services keep their names, so installed computers carry on as before.
- Installs and updates now come from the new repository address.
- A code checker on GitHub runs on every change and every Monday: the tests on Python 3.11 and 3.13, Ruff, ShellCheck, ESLint, Gitleaks and Semgrep.
- The reporter accepts only http:// or https:// addresses for the hub and XMRig.

## 0.2.9 (2026-11-10)

- An Expand all / Collapse all button next to the Look menu opens every card's full readings in the Wide and Gauges looks.
- A 24-hour chart on each card, from 5-minute averages: temperature, hashrate, power use, CPU, fan, memory, clock speed, or power sensor. Hover to see a value and its time.
- Extra power sensors: INA219, INA226, INA260 and similar chips that Linux already reads show their volts, amps, and watts. Name them with MINDFUL_FLEET_SENSOR_LABELS in the reporter's settings, such as "Fan supply".
- Updating a computer now keeps any extra lines in its reporter settings, such as sensor names or the XMRig address.

## 0.2.8 (2026-11-10)

- The header has two columns: the headline and fleet totals on the left, and a settings panel on the right that can be folded away.
- A Controls size slider scales the settings panel by itself, separate from the cards.

## 0.2.7 (2026-11-10)

- Three card looks, chosen per browser: Tall (the full card), Wide (one row per computer with the key numbers across, for big fleets), and Gauges (round dials with the number in the middle and the limit underneath).
- Wide and Gauges cards have an "All readings" button that opens the full list.

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
