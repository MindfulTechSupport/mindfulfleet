"""Find my Pis: list the Raspberry Pis on the hub's own network, including ones that don't report yet.

Read-only and standard library only. It runs only when someone presses the button on the dashboard:
1. Work out the hub's own network from the routing table (a home network, at most 1024 addresses).
2. Knock once on port 22 of every address, so the network table learns each device's hardware address.
   (A knock opens a connection and closes it at once. Nothing is logged in to or changed.)
3. Read the network table and keep the devices whose hardware address belongs to Raspberry Pi Ltd.

Pis it can't see: ones on another network, behind another router, or set to use a made-up hardware address.
"""
import ipaddress
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROUTE_FILE = "/proc/net/route"
ARP_FILE = "/proc/net/arp"
KNOCK_PORT = 22
KNOCK_SECONDS = 0.4
KNOCKERS = 64
SETTLE_SECONDS = 0.5  # Late replies to the network table's own questions.
SMALLEST_PREFIX = 22  # Bigger networks are scanned only around the hub's own address (its /24).
REST_SECONDS = 30  # Pressing the button again sooner shows the last results.

# Hardware address prefixes registered to Raspberry Pi (IEEE register, as listed by maclookup.app, aruljohn.com
# and netify.ai, checked 2026-11-11). The last two are smaller blocks (MA-M and MA-S), so they're longer.
PI_PREFIXES = (
    "b827eb",  # Raspberry Pi Foundation: Pi 1, 2, 3 and Zero
    "dca632", "e45f01", "d83add", "28cdc1",  # Raspberry Pi Trading Ltd: Pi 4, 400, 5, Zero 2, CM4
    "2ccf67", "88a29e", "98fe54",  # Raspberry Pi (Trading) Ltd: newer boards
    "f040af9", "8c1f6434a",
)


def is_raspberry_pi(mac):
    plain = "".join(c for c in str(mac).lower() if c in "0123456789abcdef")
    return len(plain) == 12 and plain.startswith(PI_PREFIXES)


def _address(hex_text):
    """/proc/net/route writes addresses as little-endian hex."""
    return ipaddress.IPv4Address(bytes.fromhex(hex_text)[::-1])


def routes(route_file=ROUTE_FILE):
    """[(interface, destination, gateway, mask)] from the kernel's routing table."""
    found = []
    try:
        lines = Path(route_file).read_text().splitlines()[1:]
    except OSError:
        return found
    for line in lines:
        fields = line.split()
        if len(fields) < 8:
            continue
        try:
            found.append((fields[0], _address(fields[1]), _address(fields[2]), _address(fields[7])))
        except ValueError:
            continue
    return found


def own_address(gateway):
    """The hub's address on the way to the router. Connecting a UDP socket sends nothing."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((str(gateway), 9))
        return ipaddress.IPv4Address(probe.getsockname()[0])


def home_network(route_file=ROUTE_FILE, find_own=own_address):
    """(network, the hub's own address) to look through, or (None, reason) when there's no safe one."""
    table = routes(route_file)
    default = next((r for r in table if int(r[1]) == 0 and int(r[3]) == 0), None)
    if not default:
        return None, "The hub isn't connected to a network with a router."
    interface, _, gateway, _ = default
    try:
        mine = find_own(gateway)
    except OSError:
        return None, "The hub couldn't work out its own address."
    network = None
    for name, destination, via, mask in table:
        if name != interface or int(via) != 0 or int(mask) == 0:
            continue
        try:
            candidate = ipaddress.IPv4Network(f"{destination}/{mask}")
        except ValueError:
            continue
        if mine in candidate:
            network = candidate
            break
    if network is None:
        network = ipaddress.IPv4Network(f"{mine}/24", strict=False)
    if network.prefixlen < SMALLEST_PREFIX:
        network = ipaddress.IPv4Network(f"{mine}/24", strict=False)
    if not network.is_private or network.is_loopback or network.is_link_local:
        return None, "Find my Pis only looks on a home or office network, and this hub's network isn't one."
    return (network, mine), None


def knock(address, port=KNOCK_PORT, timeout=KNOCK_SECONDS):
    """True when something answers on the port, False otherwise. Closes at once either way."""
    try:
        with socket.create_connection((str(address), port), timeout=timeout):
            return True
    except OSError:
        return False


def network_table(arp_file=ARP_FILE):
    """{address: hardware address} for every complete entry in the kernel's network table."""
    table = {}
    try:
        lines = Path(arp_file).read_text().splitlines()[1:]
    except OSError:
        return table
    for line in lines:
        fields = line.split()
        if len(fields) < 4:
            continue
        address, flags, mac = fields[0], fields[2], fields[3].lower()
        try:
            complete = int(flags, 16) & 0x2
        except ValueError:
            continue
        if complete and mac != "00:00:00:00:00:00":
            table[address] = mac
    return table


def find_pis(known, route_file=ROUTE_FILE, arp_file=ARP_FILE, knocker=knock, find_own=own_address,
             settle=SETTLE_SECONDS, clock=time.time):
    """known maps an address to the name of the computer reporting from it."""
    place, problem = home_network(route_file, find_own)
    if problem:
        return {"error": problem}
    network, mine = place
    addresses = [a for a in network.hosts() if a != mine]
    with ThreadPoolExecutor(max_workers=KNOCKERS) as pool:
        answered = dict(zip(addresses, pool.map(knocker, addresses), strict=True))
    if settle:
        time.sleep(settle)
    table = network_table(arp_file)
    found = []
    for address in addresses:
        mac = table.get(str(address))
        if not mac or not is_raspberry_pi(mac):
            continue
        name = known.get(str(address))
        found.append({"ip": str(address), "mac": mac, "ssh": answered[address],
                      "reporting": name is not None, "name": name})
    return {"network": str(network), "checked": len(addresses), "found": found, "when": clock()}


class Discovery:
    """One look at a time; pressing again within REST_SECONDS shows the last results."""

    def __init__(self, finder=find_pis, clock=time.time):
        self._finder = finder
        self._clock = clock
        self._busy = threading.Lock()
        self._last = None

    def run(self, known):
        """Returns the results, or None when a look is already under way."""
        if not self._busy.acquire(blocking=False):
            return None
        try:
            last = self._last
            if last and "error" not in last and self._clock() - last["when"] < REST_SECONDS:
                return {**last, "found": [{**pi, "reporting": pi["ip"] in known, "name": known.get(pi["ip"])}
                                          for pi in last["found"]]}
            self._last = self._finder(known)
            return self._last
        finally:
            self._busy.release()


def demo_results(clock=time.time):
    """For --demo: what a look might find on a home network."""
    return {"network": "192.168.1.0/24", "checked": 253, "when": clock(), "found": [
        {"ip": "192.168.1.23", "mac": "d8:3a:dd:12:34:56", "ssh": True, "reporting": False, "name": None},
        {"ip": "192.168.1.41", "mac": "b8:27:eb:65:43:21", "ssh": False, "reporting": False, "name": None},
    ]}
