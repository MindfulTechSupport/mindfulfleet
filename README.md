# Mindful Fleet

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
curl -fsSL https://raw.githubusercontent.com/MindfulTechSupport/mindful-fleet/release/install.sh | sudo bash -s -- hub
```

It prints the dashboard address and a ready-made line for adding computers. Paste that line on every computer you want to watch, including that one. Each one shows up within about 15 seconds.

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

Computers installed with 0.2.0 or earlier don't check for updates. Run this once on each one to add it:

```
curl -fsSL https://raw.githubusercontent.com/MindfulTechSupport/mindful-fleet/release/install.sh | sudo bash -s -- update
```

Before running any script from the internet, you're welcome to read it first: it's [install.sh](install.sh) in this repository.

## Arrange your dashboard

Use the **Card size** slider to fit your screen, and **Choose what cards show** to hide rows you don't need. **Arrange cards** lets you drag cards into your own order and create groups. Your layout is saved in that browser, so another computer or browser starts with the default layout.

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
- Your card layout and row choices live in one browser.
- Updates are held or skipped per computer, not from the dashboard yet.

## Security

- The reporter only sends numbers. It accepts no commands and opens no ports.
- Reports need the fleet token. Both programs run as an unprivileged user under a locked-down service.
- Traffic is plain HTTP, so **it isn't encrypted**. Anyone on your network could read the numbers and the token, and anyone who can open the dashboard can see your fleet. Use it on a home network you trust, and don't forward its port to the internet.

## Remove it

```
sudo systemctl disable --now mindful-fleet-reporter mindful-fleet-hub
sudo rm -rf /etc/systemd/system/mindful-fleet-*.service /opt/mindful-fleet /etc/mindful-fleet /var/lib/mindful-fleet
sudo userdel mindful-fleet
```

To drop one retired computer from the dashboard, stop the hub, remove that computer from `/var/lib/mindful-fleet/fleet.json`, and start the hub again.

## Run the tests

```
python3 -m unittest discover -s tests
```

## Trademark

Works with Raspberry Pi. Raspberry Pi is a trademark of Raspberry Pi Ltd. Mindful Fleet is an independent project, not made or endorsed by Raspberry Pi Ltd.
