# Add-ons

Add-ons add readings without editing the engine. One folder per add-on:

- `addon.json`: its name (the same as the folder), its kind (`reader`) and every report field it may
  add. The engine keeps only declared fields, and each one must also be in the report contract
  (`engine/report/CONTRACT.md`).
- `reader.py`: `read(settings)` returns a dict of those fields. A broken add-on is switched off with
  a plain message and everything else keeps running.

`xmrig` and `ups-nut` are the first two; their output matches the reporter's built-in readers, and a
test checks that. No product names inside: the flavor supplies settings.
