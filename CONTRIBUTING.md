# Contributing

BlackForge's catalog is generated from the official BlackArch tools page.
Do not hand-edit `src/blackforge/data/tools.json`.

```bash
python scripts/update_catalog.py
python -m pip install -e .
pytest
```

Keep package operations list-form (never `shell=True`). New health checks must
state exactly what they prove; a package or executable-file check must not be
described as a successful runtime test.

