/* MindfulFleet region and units (Settings → Region and units).
   Readings always arrive as °C, bytes and world time (UTC seconds); only what this screen shows changes.
   Plain functions with no page access, so tests/test_region.py can run them with Node. The hub checks the same
   choices in clean_layout (REGION_CHOICES and REGION_CURRENCIES in mindful_fleet_hub.py). */
(function () {
  "use strict";

  // Countries that use Fahrenheit day to day.
  const FAHRENHEIT = ["US", "LR", "MM", "BS", "BZ", "KY", "PW", "FM", "MH"];
  const EURO = ["AT", "BE", "CY", "DE", "EE", "ES", "FI", "FR", "GR", "HR", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PT", "SI", "SK"];
  const CURRENCY_OF = {
    US: "USD", CA: "CAD", GB: "GBP", AU: "AUD", NZ: "NZD", CH: "CHF", SE: "SEK", NO: "NOK", DK: "DKK", PL: "PLN",
    CZ: "CZK", HU: "HUF", JP: "JPY", CN: "CNY", IN: "INR", BR: "BRL", MX: "MXN", ZA: "ZAR", KR: "KRW", SG: "SGD",
    HK: "HKD", PH: "PHP", IL: "ILS", TR: "TRY",
  };
  const CURRENCIES = ["USD", "EUR", "GBP", "CAD", "AUD", "NZD", "CHF", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "JPY",
    "CNY", "INR", "BRL", "MXN", "ZAR", "KRW", "SGD", "HKD", "PHP", "ILS", "TRY"];
  // Day-to-day date order and clock, by country. Everyone else: day first, 24-hour clock.
  const MONTH_FIRST = ["US", "PH", "FM", "MH", "PW", "BZ"];
  const YEAR_FIRST = ["CN", "JP", "KR", "TW", "HU", "LT", "SE", "MN", "CA"];
  const TWELVE_HOUR = ["US", "CA", "AU", "NZ", "IN", "PH", "PK", "BD", "EG", "SA", "MY", "MX", "CO"];
  const CHOICES = {
    units: ["metric", "imperial"],
    dates: ["auto", "mdy", "dmy", "ymd"],
    clock: ["auto", "12", "24"],
    sizes: ["binary", "decimal"],
    speed: ["bytes", "bits"],
  };
  const TIMEZONE_RE = /^[A-Za-z][A-Za-z0-9_+-]*(\/[A-Za-z0-9_+-]+){0,2}$/;

  function countryOf(language) {
    const match = /^[a-z]{2,3}[-_]([A-Za-z]{2})\b/.exec(String(language || ""));
    return match ? match[1].toUpperCase() : "";
  }

  function workingTimezone(zone) {
    if (zone === "auto" || typeof zone !== "string" || zone.length > 40 || !TIMEZONE_RE.test(zone)) return false;
    try {
      new Intl.DateTimeFormat("en-US", { timeZone: zone }).format(0);
      return true;
    } catch {
      return false;
    }
  }

  /* Only known choices survive; anything else falls back to the metric, US-dollar starting point. */
  function clean(raw) {
    const given = raw && typeof raw === "object" ? raw : {};
    const region = {};
    for (const [key, options] of Object.entries(CHOICES)) region[key] = options.includes(given[key]) ? given[key] : options[0];
    region.country = typeof given.country === "string" && /^[A-Z]{2}$/.test(given.country) ? given.country : "";
    region.currency = CURRENCIES.includes(given.currency) ? given.currency : "USD";
    region.timezone = workingTimezone(given.timezone) ? given.timezone : "auto";
    return region;
  }

  /* What a country uses day to day (D-090: a new dashboard starts from the browser's country). */
  function forCountry(country) {
    return clean({
      country,
      units: FAHRENHEIT.includes(country) ? "imperial" : "metric",
      currency: CURRENCY_OF[country] || (EURO.includes(country) ? "EUR" : "USD"),
    });
  }

  function guess(language) {
    return forCountry(countryOf(language));
  }

  function make(raw, browserLanguage) {
    const region = clean(raw);
    const country = region.country || countryOf(browserLanguage);
    const fahrenheit = region.units === "imperial";
    const dates = region.dates !== "auto" ? region.dates
      : MONTH_FIRST.includes(country) ? "mdy" : YEAR_FIRST.includes(country) ? "ymd" : "dmy";
    const twelve = region.clock !== "auto" ? region.clock === "12" : TWELVE_HOUR.includes(country);
    const timeZone = region.timezone === "auto" ? undefined : region.timezone;

    const number = (value, digits) => new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
    const temp = (celsius) => (fahrenheit ? celsius * 9 / 5 + 32 : celsius);
    // A difference between two temperatures: 20 °C cooler is 36 °F cooler, never 68.
    const tempChange = (celsius) => (fahrenheit ? celsius * 9 / 5 : celsius);
    const tempUnit = fahrenheit ? "°F" : "°C";

    function parts(seconds) {
      const found = {};
      const pieces = new Intl.DateTimeFormat("en-US", {
        timeZone, year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", hourCycle: "h23",
      }).formatToParts(new Date(seconds * 1000));
      for (const piece of pieces) found[piece.type] = piece.value;
      found.monthNumber = String(new Intl.DateTimeFormat("en-US", { timeZone, month: "numeric" }).format(new Date(seconds * 1000))).padStart(2, "0");
      return found;
    }

    function time(seconds) {
      const p = parts(seconds);
      const hour = Number(p.hour) % 24;
      if (!twelve) return `${String(hour).padStart(2, "0")}:${p.minute}`;
      return `${hour % 12 || 12}:${p.minute} ${hour < 12 ? "AM" : "PM"}`;
    }

    function date(seconds, withTime) {
      const p = parts(seconds);
      let day;
      if (dates === "ymd") day = `${p.year}-${p.monthNumber}-${String(p.day).padStart(2, "0")}`;
      else if (dates === "mdy") day = withTime ? `${p.month} ${p.day}` : `${p.month} ${p.day}, ${p.year}`;
      else day = withTime ? `${p.day} ${p.month}` : `${p.day} ${p.month} ${p.year}`;
      return withTime ? `${day}, ${time(seconds)}` : day;
    }

    function size(bytes) {
      if (bytes == null) return "";
      const step = region.sizes === "decimal" ? 1000 : 1024;
      const gb = bytes / step ** 3;
      if (gb < 1) return `${Math.round(bytes / step ** 2)} MB`;
      return gb >= 100 ? `${number(gb, 0)} GB` : `${number(gb, 1)} GB`;
    }

    function speed(bytesPerSecond) {
      if (bytesPerSecond == null) return "—";
      if (region.speed === "bits") {
        const bits = bytesPerSecond * 8;
        if (bits >= 1e6) return `${number(bits / 1e6, 1)} Mbps`;
        if (bits >= 1e3) return `${Math.round(bits / 1e3)} kbps`;
        return `${Math.round(bits)} bps`;
      }
      if (bytesPerSecond >= 1024 ** 2) return `${number(bytesPerSecond / 1024 ** 2, 1)} MB/s`;
      if (bytesPerSecond >= 1024) return `${Math.round(bytesPerSecond / 1024)} KB/s`;
      return `${bytesPerSecond} B/s`;
    }

    function money(amount, digits) {
      return new Intl.NumberFormat("en-US", { style: "currency", currency: region.currency, minimumFractionDigits: digits, maximumFractionDigits: digits }).format(amount);
    }

    // The hub writes temperatures into its sentences in °C ("It's running warm at 72°C."); show them in the chosen unit.
    function words(text) {
      if (!fahrenheit) return text;
      return String(text).replace(/(-?\d+(?:\.\d+)?)\s?°C/g, (whole, value) => `${Math.round(temp(Number(value)))}°F`);
    }

    return { region, fahrenheit, tempUnit, temp, tempChange, number, time, date, size, speed, money, words };
  }

  window.MF_REGION = { clean, guess, forCountry, make, countryOf, CHOICES, CURRENCIES, CURRENCY_OF, EURO, FAHRENHEIT };
})();
