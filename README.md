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

Pick one computer to run the dashboard. Copy this folder to it, then:

```
sudo ./install/install-hub.sh
```

It prints the dashboard address, the hub address, and your fleet token.

On every computer you want to watch, including that one, copy this folder over and run:

```
sudo ./install/install-reporter.sh http://HUB-ADDRESS:8742
```

Paste the token when asked. The computer shows up on the dashboard within about 15 seconds.

To copy the folder from another computer:

```
scp -r mindful-fleet USERNAME@PI-ADDRESS:
ssh -t USERNAME@PI-ADDRESS "cd mindful-fleet && sudo ./install/install-reporter.sh http://HUB-ADDRESS:8742"
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

A computer shows as offline after 60 seconds without a report.

CPU load never changes the color, because some computers, such as miners, are meant to run flat out. A fan at rest on a cool Pi 5 is also normal. "Earlier" warnings clear when the computer restarts, since that's when the firmware resets them.

## Known issues in 0.1.0

- USB fans can't report their speed, so they show "No reading".
- Some boards report 0 RPM with no fan on the fan connector. They show "Resting", then wrongly warn that the fan isn't spinning at 60°C.
- Cards are always in alphabetical order, and their size is fixed.
- The temperature trend line makes tiny changes look bigger than they are.
- The token prompt hides what you paste. If a report is refused with HTTP 401, run the installer again and paste carefully.

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
