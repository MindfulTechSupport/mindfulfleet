# MindfulFleet

A friendly health dashboard that puts your whole fleet of small computers on one screen. Works with Raspberry Pi.

Each computer gets a card with a little face that shows how it's doing: temperature, whether it's slowing itself down, fan speed, CPU, memory, storage, power warnings, and how long it's been running. A computer that stops checking in shows as offline instead of vanishing.

It needs only Python 3, which Raspberry Pi OS already includes. There's nothing else to install, no account, and nothing leaves your network.

## See it first

No Pi needed. This runs a pretend fleet on your own computer:

```
python3 hub/mindful_fleet_hub.py --demo
```

Then open http://127.0.0.1:8742/

## Install it

On the computer that will run the dashboard, paste:

```
curl -fsSL https://raw.githubusercontent.com/MindfulTechSupport/mindfulfleet/release/install.sh | sudo bash -s -- hub
```

It prints the dashboard address and a ready-made line for adding computers. Paste that line on every computer you want to watch, including that one. Each one shows up within about 15 seconds.

## Dashboard password

Give the dashboard a password so only you can open it. On the hub, run:

```
sudo mindful-fleet password
```

Choose at least 10 characters; a short sentence you'll remember works well. From then on the dashboard asks for it, and each browser stays signed in for 30 days. You can change it, or sign out, under Settings → Password and sign out. Changing it signs out every other browser.

- Forgot it? Run `sudo mindful-fleet password` on the hub again to choose a new one.
- To turn the password off, run `sudo mindful-fleet password off` on the hub. It keeps the old password in a file so you can put it back, and tells you how.
- Until you set one, the dashboard stays open to anyone on your network, as before, and shows a reminder.
- After a few wrong guesses, the dashboard makes you wait, longer each time, up to 5 minutes.
- The hub keeps only a salted hash of the password (PBKDF2-SHA256), never the password itself.

## Updates

Each computer checks for a new version once a day, at a random time so a whole fleet doesn't restart at once, and updates itself. Only versions on the `release` branch are installed. Day-to-day work on `main` never reaches your computers.

To control updates on a computer:

| Command | What it does |
|---|---|
| `mindful-fleet status` | Shows the installed version, the latest version, and the update setting |
| `sudo mindful-fleet update` | Updates now |
| `sudo mindful-fleet hold` | Stops automatic updates, so this computer stays on its current version |
| `sudo mindful-fleet skip` | Skips the latest version, and takes the next one after it |
| `sudo mindful-fleet resume` | Turns automatic updates back on and clears any skip |
| `sudo mindful-fleet xmrig` | Turns on XMRig's read-only API so the card shows hashrate. See Mining |
| `sudo mindful-fleet every 6h` | Checks for updates every 6 hours. Also `hourly`, `daily` (the default), or `weekly` |
| `sudo mindful-fleet password` | On the hub: chooses or changes the dashboard password. `password off` turns it off |

Computers installed with 0.2.0 or earlier don't check for updates. Run this once on each one to add it:

```
curl -fsSL https://raw.githubusercontent.com/MindfulTechSupport/mindfulfleet/release/install.sh | sudo bash -s -- update
```

Before running any script from the internet, you're welcome to read it first: it's [install.sh](install.sh) in this repository.

## Arrange your dashboard

Pick a **Look**: Tall shows every reading, Wide puts each computer on one row for big fleets, and Gauges shows round dials. Use the **Card width**, **Text size** and **Card height** sliders to fit your screen. With a set height, each card scrolls to show the rest. Use **Choose what cards show** to hide rows you don't need. **Arrange cards** lets you drag cards into your own order and create groups. Your card order, groups, and row choices are saved on the hub, so every browser sees them. Card width, text size and height are saved per browser, so a phone and a big screen can differ.

In the Wide and Gauges looks, **Expand all** opens every card's full readings at once, and **Collapse all** closes them again. You can still open or close one card on its own.

Each card has a **Last 24 hours** chart. Pick a reading from its menu (temperature, hashrate, power use, CPU, fan, memory, clock speed, or a power sensor) and hover over the line to see the value at any time. The hub keeps one average per 5 minutes, so a new computer's chart fills in over the first day.

## Power sensors (optional)

The Pi 5 measures its own power, but not what a fan or other add-on draws from a separate supply. An INA219, INA226 or INA260 power sensor board on the Pi's I2C pins can measure that. Once Linux reads the sensor (turn on I2C with `sudo raspi-config` and add the sensor's line to `/boot/firmware/config.txt`, such as `dtoverlay=i2c-sensor,ina219`), the card shows its volts, amps and watts with no other setup.

To give sensors names, add a line like this to `/etc/mindful-fleet/reporter.env` and run `sudo systemctl restart mindful-fleet-reporter`:

```
MINDFUL_FLEET_SENSOR_LABELS=Fan supply,Pump
```

Names are used in the order the sensors are found. This support hasn't been tested on real sensor hardware yet.

## Mining

If a computer runs the XMRig miner, its card can show hashrate, cores mining, shares accepted, and hashes per watt. XMRig shares these through its built-in web API, which is off by default. To turn it on, run this on each miner:

```
sudo mindful-fleet xmrig
```

It finds XMRig, shows exactly what it will change, and asks before changing anything. It turns the API on for that computer only (`127.0.0.1`, port 37841) in read-only mode, keeps a backup of XMRig's settings, and restarts the miner. Your wallet address isn't read or sent.

To set it up by hand instead, add this to XMRig's `config.json` and restart it:

```
"http": { "enabled": true, "host": "127.0.0.1", "port": 37841, "access-token": null, "restricted": true }
```

## What the colors mean

| Reading | Needs a look | Needs help now |
|---|---|---|
| Temperature | 70°C or more | 80°C or more |
| Speed | Holding back, or slowed down since the last restart | Slowing down right now |
| Power | Low voltage since the last restart | Low voltage right now |
| Fan | Not spinning at 60°C or more | |
| Memory | 85% full | 95% full |
| Storage | 80% full | 90% full |

A computer shows as offline after 60 seconds without a report. A core counts as in use at 50% load or more.

CPU load never changes the color, because some computers, such as miners, are meant to run flat out. A fan at rest on a cool Pi 5 is also normal. "Earlier" warnings clear when the computer restarts, since that's when the firmware resets them.

## Known limits

- USB fans can't report their speed, so they show "Not reported".
- A fan's "Fan effort" and the "Voltage" row only appear on boards that report them. Voltage needs a Pi 5.
- Anyone who can open the dashboard can change its shared layout. Set a password to keep others out.
- Signing in again is needed after the hub restarts.
- Power use is the board's own use as its power chip measures it, on Pi 5 boards only. It leaves out USB devices, fans on their own supply, and power-supply losses, so a wall meter reads higher.
- Averages cover only the history the hub has. After a hub restart, it picks up from its last save, up to 5 minutes old.
- Updates are held or skipped per computer, not from the dashboard yet.

## Security

- The reporter only sends numbers. It accepts no commands and opens no ports.
- Reports need the fleet token. Both programs run as an unprivileged user under a locked-down service.
- The hub contacts GitHub every 6 hours to see whether a newer version exists. It sends nothing about your fleet. Start it with `--no-update-check` to turn this off.
- The reporter sends the name of the busiest program on each computer to the hub.
- The dashboard password keeps people on your network out of the dashboard. Its sign-in cookie can't be read by scripts and isn't sent by other websites, and the hub refuses changes sent from other sites.
- Traffic is plain HTTP, so **it isn't encrypted**. Anyone on your network could read the numbers, the token and, while you sign in, the password. Use it on a home network you trust, and don't forward its port to the internet. Encrypted links are planned.

## Remove it

```
sudo systemctl disable --now mindful-fleet-reporter mindful-fleet-hub
sudo rm -rf /etc/systemd/system/mindful-fleet-*.service /opt/mindful-fleet /etc/mindful-fleet /var/lib/mindful-fleet
sudo userdel mindful-fleet
```

To drop one retired computer from the dashboard, stop the hub, remove that computer from `/var/lib/mindful-fleet/fleet.json`, and start the hub again.

## Run the tests

The code checker on GitHub (Actions → "Code checker") runs these tests and the style and security checkers on every change and every Monday. To run the tests yourself:

```
python3 -m unittest discover -s tests
```

## Trademark

Works with Raspberry Pi. Raspberry Pi is a trademark of Raspberry Pi Ltd. MindfulFleet is an independent project, not made or endorsed by Raspberry Pi Ltd.
