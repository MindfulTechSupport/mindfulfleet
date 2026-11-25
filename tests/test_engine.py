"""The Fleet Engine (doc 22, Step 1): the report contract, the flavor, add-ons and the engine's clean edges."""

import ast
import json
import re
import sys
import tempfile
import threading
import unittest
import unittest.mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "hub"), str(ROOT / "reporter")]

import mindful_fleet_hub as hub  # noqa: E402
import mindful_fleet_reporter as reporter  # noqa: E402
import plan  # noqa: E402
from engine import addons as engine_addons  # noqa: E402
from engine import flavor as engine_flavor  # noqa: E402
from engine.report import contract  # noqa: E402

BASE = {"id": "a1b2c3d4", "hostname": "pi-one"}


def cleaned(**fields):
    return hub.clean_report({**BASE, **fields})


class ReportContractTests(unittest.TestCase):
    """The hub keeps exactly the contract's fields and holds each one to its limits."""

    def test_hub_keeps_exactly_the_contract_fields_in_order(self):
        self.assertEqual(list(cleaned()), contract.field_names())
        self.assertNotIn("wallet", cleaned(wallet="4xyz", password="pw"))

    def test_contract_page_matches_the_code(self):
        page = (ROOT / "engine" / "report" / "CONTRACT.md").read_text(encoding="utf-8")
        self.assertEqual(page, contract.contract_markdown())

    def test_number_fields_take_their_limits_and_refuse_beyond(self):
        for f in contract.FIELDS:
            if f.kind != "number":
                continue
            with self.subTest(field=f.name):
                self.assertEqual(cleaned(**{f.name: f.high})[f.name], f.high)
                self.assertEqual(cleaned(**{f.name: f.low})[f.name], f.low)
                for bad in (f.high * 2 + 1, f.low - 1, True, "5", float("nan"), float("inf")):
                    with self.assertRaises(hub.BadReport):
                        cleaned(**{f.name: bad})

    def test_number_lists_hold_count_and_range(self):
        for f in contract.FIELDS:
            if f.kind != "numbers":
                continue
            with self.subTest(field=f.name):
                self.assertEqual(cleaned(**{f.name: [f.low] * f.count})[f.name], [f.low] * f.count)
                for bad in ([f.low] * (f.count + 1), [f.high * 2 + 1], [f.low - 1], "1,2"):
                    with self.assertRaises(hub.BadReport):
                        cleaned(**{f.name: bad})

    def test_text_is_cut_to_its_length(self):
        for f in contract.FIELDS:
            if f.kind in ("short", "text"):
                with self.subTest(field=f.name):
                    self.assertEqual(len(cleaned(**{f.name: "x" * (f.length + 50)})[f.name]), f.length)
                    self.assertIsNone(cleaned(**{f.name: 12})[f.name])

    def test_counts_and_choices_drop_bad_values_without_refusing(self):
        for f in contract.FIELDS:
            with self.subTest(field=f.name):
                if f.kind == "count":
                    self.assertEqual(cleaned(**{f.name: f.high})[f.name], f.high)
                    self.assertIsNone(cleaned(**{f.name: f.high + 1})[f.name])
                    self.assertIsNone(cleaned(**{f.name: -1})[f.name])
                elif f.kind == "choice":
                    for choice in f.choices:
                        self.assertEqual(cleaned(**{f.name: choice})[f.name], choice)
                    self.assertIsNone(cleaned(**{f.name: "sometimes"})[f.name])

    def test_identity_fields_refuse_bad_values(self):
        for bad in ({"id": "NOT-HEX"}, {"id": "a1b2"}, {"hostname": "-bad"}, {"hostname": "x" * 64}, {"ip": "192.0.2.1; rm"}):
            with self.subTest(bad=bad), self.assertRaises(hub.BadReport):
                hub.clean_report({**BASE, **bad})
        self.assertEqual(cleaned(ip="192.0.2.10")["ip"], "192.0.2.10")

    def test_sensors_and_ups_follow_the_contract(self):
        sensor = {"label": "Fan line", "chip": "ina219", "volts": 5.1, "amps": 0.4, "watts": 2.0}
        self.assertEqual(len(cleaned(sensors=[sensor] * 8)["sensors"]), 8)
        with self.assertRaises(hub.BadReport):
            cleaned(sensors=[sensor] * 9)
        ups = {"name": "tripplite", "status": "OB DISCHRG", "charge": 84.0, "runtime_s": 1500.0, "load": 13.0}
        self.assertEqual(cleaned(ups=ups)["ups"], ups)
        self.assertIsNone(cleaned(ups={"status": "on battery!"})["ups"])

    def test_every_field_has_a_known_kind_and_a_meaning(self):
        kinds = {"id", "hostname", "address", "short", "text", "number", "numbers", "count", "choice", "sensors", "ups"}
        names = [f.name for f in contract.FIELDS]
        self.assertEqual(len(names), len(set(names)))
        for f in contract.FIELDS:
            self.assertIn(f.kind, kinds, f.name)
            self.assertTrue(f.meaning, f.name)


class FlavorTests(unittest.TestCase):
    """The MindfulFleet flavor matches what installed Pis use today, so switching to it changes nothing."""

    def setUp(self):
        self.flavor = engine_flavor.load_flavor(ROOT / "flavors" / "mindfulfleet")

    def test_matches_the_hub_and_the_installers(self):
        f = self.flavor
        self.assertEqual(f["port"], hub.DEFAULT_PORT)
        self.assertEqual(f"https://raw.githubusercontent.com/{f['updates']['repo']}/{f['updates']['branch']}/VERSION",
                         hub.RELEASE_VERSION_URL)
        installers = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "install").iterdir() if p.is_file())
        for path in (f["app_dir"], f["settings_dir"], f["state_dir"]):
            self.assertIn(path + "/", installers)
        for part in ("hub", "reporter", "update"):
            self.assertIn(f"{f['service_prefix']}-{part}", installers)
        self.assertTrue((ROOT / "install" / f["command"]).is_file())
        self.assertEqual(plan.DEFAULT_PATH, f["settings_dir"] + "/plan")

    def test_plans_match_the_hub(self):
        mapped = {name: {"plus": "plus" in feats, "pro": "pro" in feats} for name, feats in self.flavor["plans"].items()}
        self.assertEqual(mapped, plan.PLANS)

    def test_legal_footer_is_on_the_dashboard(self):
        page = (ROOT / "hub" / "dashboard" / "index.html").read_text(encoding="utf-8")
        self.assertIn(self.flavor["legal"]["footer"], page)

    def test_console_is_off_until_the_owner_turns_it_on(self):
        self.assertEqual(self.flavor["console"], {"allowed": True, "default": "off"})

    def test_words(self):
        words = self.flavor["words"]
        self.assertEqual(engine_flavor.say(words, "device"), "Pi")
        self.assertEqual(engine_flavor.say(words, "device", 3), "Pis")
        self.assertEqual(engine_flavor.say(words, "product"), "MindfulFleet")
        self.assertEqual(engine_flavor.say(words, "no-such-word"), "no-such-word")

    def test_broken_flavors_stop_with_a_plain_message(self):
        good = json.loads((ROOT / "flavors" / "mindfulfleet" / "flavor.json").read_text(encoding="utf-8"))
        broken = [
            {"port": 80}, {"port": "8742"}, {"port": True}, {"name": "Bad Name"}, {"app_dir": "/opt/../etc"},
            {"updates": {"source": "ftp"}}, {"updates": {"source": "github", "repo": "nope"}},
            {"addons": ["xmrig", "xmrig"]}, {"addons": ["../evil"]}, {"console": {"allowed": False, "default": "on"}},
            {"product": ""},
        ]
        for change in broken:
            with self.subTest(change=change), self.assertRaises(engine_flavor.FlavorError) as caught:
                engine_flavor.check_flavor({**good, **change})
            self.assertIn("flavor.json", str(caught.exception))
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(engine_flavor.FlavorError) as caught:
                engine_flavor.load_flavor(folder)
            self.assertIn("missing flavor.json", str(caught.exception))
            (Path(folder) / "flavor.json").write_text("{not json", encoding="utf-8")
            (Path(folder) / "words.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(engine_flavor.FlavorError):
                engine_flavor.load_flavor(folder)

    def test_a_hubless_flavor_with_its_own_update_source_is_allowed(self):
        good = json.loads((ROOT / "flavors" / "mindfulfleet" / "flavor.json").read_text(encoding="utf-8"))
        node = {**good, "name": "example-node", "product": "Example node", "updates": {"source": "addon"},
                "console": {"allowed": False, "default": "off"}, "plans": {"free": []}}
        self.assertEqual(engine_flavor.check_flavor(node)["updates"], {"source": "addon"})


class AddonTests(unittest.TestCase):
    """Add-ons add readings without editing the engine; a broken one is switched off, never fatal."""

    def test_first_addons_load_and_match_the_contract(self):
        loaded, problems = engine_addons.load_addons(ROOT / "addons", ["xmrig", "ups-nut"])
        self.assertEqual(problems, [])
        self.assertEqual([a.name for a in loaded], ["xmrig", "ups-nut"])
        for addon in loaded:
            with self.subTest(addon=addon.name):
                self.assertEqual(sorted(addon.fields), sorted(contract.field_names(addon.name)))

    def make_addon(self, folder, name, manifest, code):
        here = Path(folder) / name
        here.mkdir()
        (here / "addon.json").write_text(json.dumps(manifest), encoding="utf-8")
        (here / "reader.py").write_text(code, encoding="utf-8")

    def test_broken_addons_are_switched_off_and_the_rest_keep_running(self):
        ok = {"name": "good", "kind": "reader", "fields": {"temp_c": {"type": "number"}}}
        with tempfile.TemporaryDirectory() as folder:
            self.make_addon(folder, "good", ok, "def read(settings):\n    return {'temp_c': 41.5, 'wallet': 'secret'}\n")
            self.make_addon(folder, "wrongname", {**ok, "name": "other"}, "def read(s):\n    return {}\n")
            self.make_addon(folder, "nofields", {**ok, "name": "nofields", "fields": {}}, "def read(s):\n    return {}\n")
            self.make_addon(folder, "noread", {**ok, "name": "noread"}, "x = 1\n")
            self.make_addon(folder, "crashes", {**ok, "name": "crashes"}, "raise RuntimeError('boom')\n")
            self.make_addon(folder, "fails", {**ok, "name": "fails"}, "def read(s):\n    raise OSError('gone')\n")
            names = ["good", "wrongname", "nofields", "noread", "crashes", "missing", "../good", "fails"]
            loaded, problems = engine_addons.load_addons(folder, names)
            self.assertEqual([a.name for a in loaded], ["good", "fails"])
            self.assertEqual(len(problems), 6)
            self.assertTrue(all(p.startswith("The add-on") for p in problems))
            readings, problems = engine_addons.run_readers(loaded)
            self.assertEqual(readings, {"temp_c": 41.5})  # The undeclared field is dropped.
            self.assertEqual(problems, ["The add-on 'fails' failed this time (OSError)."])


class AddonParityTests(unittest.TestCase):
    """The add-ons give exactly what the reporter's built-in readers give, so moving to them changes nothing."""

    @classmethod
    def setUpClass(cls):
        loaded, _ = engine_addons.load_addons(ROOT / "addons", ["xmrig", "ups-nut"])
        cls.xmrig, cls.ups = loaded

    def serve(self, routes, token=None):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if token and self.headers.get("Authorization") != f"Bearer {token}":
                    self.send_response(401)
                    self.end_headers()
                    return
                body = routes.get(self.path)
                self.send_response(200 if body is not None else 404)
                self.end_headers()
                if body is not None:
                    self.wfile.write(json.dumps(body).encode())

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def test_xmrig_matches(self):
        summary = {"hashrate": {"total": [1210.5, 1198.2, 1201.0]}, "results": {"shares_good": 99, "shares_total": 100},
                   "connection": {"pool": "pool.example.com:10128", "tls": "TLSv1.3"}, "algo": "rx/0",
                   "version": "6.24.0", "worker_id": "rig01"}
        cases = [
            ({"/2/summary": summary, "/2/backends": [{"type": "cpu", "enabled": True, "threads": [{}, {}]}]}, "secret", "secret"),
            ({"/2/summary": summary, "/2/backends": [{"type": "cpu", "enabled": True}]}, "secret", "wrong"),
            ({"/2/summary": {"hashrate": {"total": [None, None, None]}, "connection": {"pool": "p.example.com:1", "tls": None}}},
             None, None),
            ({"/2/summary": ["not", "an", "object"]}, None, None),
            ({"/2/summary": summary, "/2/backends": "garbage"}, None, None),
        ]
        for routes, token, sent in cases:
            url = self.serve(routes, token)
            with self.subTest(routes=sorted(routes), sent=sent):
                built_in = reporter.read_xmrig(url, sent)
                self.assertEqual(self.xmrig.read({"url": url, "token": sent}), built_in)
        for url in ("off", "", "file:///etc/passwd", "http://127.0.0.1:9"):
            with self.subTest(url=url):
                self.assertEqual(self.xmrig.read({"url": url}), reporter.read_xmrig(url))

    def test_ups_matches(self):
        upsc = "battery.charge: 84\nbattery.runtime: 1500\nups.load: 13\nups.status: OB DISCHRG\n"
        cases = [
            ({"upsc -l": ("tripplite\n", 0), "upsc tripplite@localhost": (upsc, 0)}, None),
            ({"upsc -l": ("", 1)}, None),
            ({"upsc myups@localhost": ("battery.charge: nan\nups.status: OL\n", 0)}, "myups@localhost"),
            ({"upsc myups@localhost": ("", 1)}, "myups@localhost"),
        ]

        def fake(outputs):
            def run(cmd, **kwargs):
                out, code = outputs[" ".join(cmd)]
                return type("Done", (), {"stdout": out, "returncode": code})()
            return run

        for outputs, name in cases:
            with self.subTest(outputs=sorted(outputs)), unittest.mock.patch("shutil.which", return_value="/usr/bin/upsc"):
                built_in = reporter.read_ups(fake(outputs), name)
                expected = {"ups": built_in} if built_in is not None else {}
                self.assertEqual(self.ups.read({"run": fake(outputs), "name": name}), expected)
        with unittest.mock.patch("shutil.which", return_value=None):
            self.assertEqual(self.ups.read({}), {})


class CleanEdgeTests(unittest.TestCase):
    """No product names inside the engine or the add-ons, MIT headers, and standard-library imports only."""

    SHARED = ("engine", "addons")

    def files(self, *suffixes):
        for top in self.SHARED:
            for path in sorted((ROOT / top).rglob("*")):
                if path.is_file() and path.suffix in suffixes and "__pycache__" not in path.parts:
                    yield path

    def test_no_product_names_inside(self):
        brand = re.compile(r"mindful|/opt/|/etc/[a-z]|raspberry pi ltd", re.IGNORECASE)
        for path in self.files(".py", ".json", ".md"):
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertIsNone(brand.search(path.read_text(encoding="utf-8")))

    def test_every_code_file_has_the_mit_header(self):
        for path in self.files(".py"):
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertTrue(path.read_text(encoding="utf-8").startswith("# SPDX-License-Identifier: MIT\n"))
        self.assertTrue((ROOT / "engine" / "LICENSE").read_text(encoding="utf-8").startswith("MIT License"))

    def test_standard_library_only(self):
        allowed = set(sys.stdlib_module_names) | {"engine"}
        for path in self.files(".py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    tops = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    tops = [(node.module or "").split(".")[0]]
                else:
                    continue
                for top in tops:
                    with self.subTest(path=str(path.relative_to(ROOT)), module=top):
                        self.assertIn(top, allowed)
                        if path.parts[len(ROOT.parts)] == "addons":
                            self.assertNotEqual(top, "engine")


if __name__ == "__main__":
    unittest.main()
