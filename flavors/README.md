# Flavors

A flavor is everything that makes the Fleet Engine a particular product: its name, the words its
screens use, its folders, port and service names, where it gets updates, which add-ons it switches
on, its plans and whether the central console is allowed. One folder per flavor:

- `flavor.json`: the settings, checked by `engine/flavor.py` (a broken one stops with a plain message)
- `words.json`: each word as `[one, many]` or a single string

`mindfulfleet/` is the first flavor. Its values match what installed MindfulFleet Pis use today, and
a test keeps them equal, so switching the hub and reporter to read the flavor changes nothing on a Pi.
