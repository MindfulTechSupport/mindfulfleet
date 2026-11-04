"""Turns raw numbers from a reporter into plain-English health. No I/O, so it's easy to test."""

import math

OK, WARN, BAD, OFFLINE = "ok", "warn", "bad", "offline"
_RANK = {OK: 0, WARN: 1, BAD: 2, OFFLINE: 3}

OFFLINE_AFTER_SECONDS = 60

TEMP_WARN, TEMP_BAD = 70, 80
FAN_EXPECTED_ABOVE = 60  # A Pi 5's fan can rest when cool, so 0 RPM is only odd once it's warm.
CORE_BUSY_PERCENT = 50
MEM_WARN, MEM_BAD = 85, 95
DISK_WARN, DISK_BAD = 80, 90

# Bits reported by `vcgencmd get_throttled`.
UNDERVOLT_NOW = 1 << 0
FREQ_CAPPED_NOW = 1 << 1
THROTTLED_NOW = 1 << 2
SOFT_TEMP_NOW = 1 << 3
UNDERVOLT_SINCE_BOOT = 1 << 16
FREQ_CAPPED_SINCE_BOOT = 1 << 17
THROTTLED_SINCE_BOOT = 1 << 18
SOFT_TEMP_SINCE_BOOT = 1 << 19


def _whole(value):
    """Round .5 up, the way the dashboard does, so a card never shows two temperatures."""
    return math.floor(value + 0.5)


def _worst(*levels):
    return max(levels, key=_RANK.__getitem__)


def _percent(used, total):
    if used is None or not total:
        return None
    return round(100 * used / total, 1)


def _band(value, warn, bad):
    if value is None:
        return OK
    return BAD if value >= bad else WARN if value >= warn else OK


def assess_throttle(bits):
    """Return (speed_level, speed_text, power_level, power_text, issues)."""
    if bits is None:
        return OK, "Not available", OK, "Not available", []

    issues = []
    slow_now = bits & (THROTTLED_NOW | FREQ_CAPPED_NOW | SOFT_TEMP_NOW)
    slow_before = bits & (THROTTLED_SINCE_BOOT | FREQ_CAPPED_SINCE_BOOT | SOFT_TEMP_SINCE_BOOT)

    if bits & THROTTLED_NOW:
        speed = (BAD, "Slowed down right now")
        issues.append((BAD, "It's slowing itself down right now."))
    elif slow_now:
        speed = (WARN, "Holding back to stay cool")
        issues.append((WARN, "It's holding back its top speed to stay cool."))
    elif slow_before:
        speed = (WARN, "Slowed down earlier")
        issues.append((WARN, "It slowed itself down at some point since its last restart."))
    else:
        speed = (OK, "Full speed")

    if bits & UNDERVOLT_NOW:
        power = (BAD, "Low voltage right now")
        issues.append((BAD, "Its power supply isn't keeping up right now. Check the adapter and cable."))
    elif bits & UNDERVOLT_SINCE_BOOT:
        power = (WARN, "Low voltage earlier")
        issues.append((WARN, "Its power dipped since its last restart. Keep an eye on the adapter and cable."))
    else:
        power = (OK, "Steady")

    return speed[0], speed[1], power[0], power[1], issues


def assess_cpu(cores):
    """Average load, and how many cores count as in use."""
    if not cores:
        return {"average": None, "busy": 0, "total": 0}
    return {
        "average": round(sum(cores) / len(cores), 1),
        "busy": sum(1 for load in cores if load >= CORE_BUSY_PERCENT),
        "total": len(cores),
    }


def assess_fan(fan, percent, temp, fan_max_rpm):
    """Return (level, text, percent, issue). Some boards report 0 RPM with no fan attached,
    so 0 only counts as a stopped fan on a Pi whose fan has been seen spinning."""
    if fan is None:
        return OK, "Not reported", None, None
    if fan > 0:
        if percent is None and fan_max_rpm:
            percent = min(100, round(100 * fan / fan_max_rpm))
        return OK, f"{fan:,} RPM", percent, None
    if not fan_max_rpm:
        return OK, "No fan detected", None, None
    if temp is not None and temp >= FAN_EXPECTED_ABOVE:
        issue = f"The fan isn't spinning at {_whole(temp)}°C. Check that the cooler is plugged in."
        return WARN, "Not spinning", 0, issue
    return OK, "Resting", 0, None


def assess(report, age_seconds, fan_max_rpm=0):
    """Return the health summary shown on a card."""
    issues = []
    temp = report.get("temp_c")
    fan = report.get("fan_rpm")
    mem_pct = _percent(report.get("mem_used"), report.get("mem_total"))
    disk_pct = _percent(report.get("disk_used"), report.get("disk_total"))

    temp_level = _band(temp, TEMP_WARN, TEMP_BAD)
    if temp_level == BAD:
        issues.append((BAD, f"It's overheating at {_whole(temp)}°C."))
    elif temp_level == WARN:
        issues.append((WARN, f"It's running warm at {_whole(temp)}°C."))

    fan_level, fan_text, fan_percent, fan_issue = assess_fan(fan, report.get("fan_percent"), temp, fan_max_rpm)
    if fan_issue:
        issues.append((fan_level, fan_issue))

    speed_level, speed_text, power_level, power_text, throttle_issues = assess_throttle(report.get("throttled"))
    issues.extend(throttle_issues)

    mem_level = _band(mem_pct, MEM_WARN, MEM_BAD)
    if mem_level != OK:
        issues.append((mem_level, f"Memory is {_whole(mem_pct)}% full."))

    disk_level = _band(disk_pct, DISK_WARN, DISK_BAD)
    if disk_level != OK:
        issues.append((disk_level, f"Storage is {_whole(disk_pct)}% full."))

    status = _worst(temp_level, fan_level, speed_level, power_level, mem_level, disk_level)

    online = age_seconds < OFFLINE_AFTER_SECONDS
    if not online:
        status = OFFLINE
        issues = [(OFFLINE, f"Hasn't checked in for {humanize_seconds(age_seconds)}.")]

    issues.sort(key=lambda item: -_RANK[item[0]])
    return {
        "status": status,
        "online": online,
        "issues": [{"level": level, "text": text} for level, text in issues],
        "temp": {"level": temp_level, "value": temp},
        "fan": {"level": fan_level, "text": fan_text, "percent": fan_percent},
        "speed": {"level": speed_level, "text": speed_text},
        "power": {"level": power_level, "text": power_text, "volts": report.get("input_volts")},
        "cpu": assess_cpu(report.get("cpu_per_core")),
        "memory": {"level": mem_level, "percent": mem_pct},
        "disk": {"level": disk_level, "percent": disk_pct},
    }


def humanize_seconds(seconds):
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds} seconds"
    if seconds < 5400:
        return f"{round(seconds / 60)} minutes"
    if seconds < 129600:
        return f"{round(seconds / 3600)} hours"
    return f"{round(seconds / 86400)} days"
