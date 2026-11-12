# PicoScope CSV importer

PicoScope CSV exports are detected from their `Time;Channel A;...` header and
parenthesized units row. The PicoScope CSV importer supports semicolon/decimal-comma,
comma/decimal-point, and tab-separated exports. Channel values retain their source
units; time is stored as device-relative milliseconds, including negative
pre-trigger samples. Missing or non-finite channel samples become gaps (NaN).
Malformed rows and backwards time fail with a line number; export separate
captures as separate files. Blank units in the plugin's `importer.json` use the
CSV units automatically; groups and visibility remain editable there.

Plugins are disabled by default. Enable the plugin under
**Settings > Plugins > PicoScope CSV importer**. Use the normal folder import
action or `python -m src.jobs.import_data "path/to/capture.csv"` to create its
HDF5 cache.

## Installation and compatibility

Plugin version 0.1.0 supports [LODGaZilla](https://github.com/RikuRaitis/LODGaZilla)
plugin API 1. This is an independent repository; its installation directory
must use the manifest ID `picoscope_csv_importer`. With the application checked out at
`HOST`, install it using:

```powershell
git clone https://github.com/RikuRaitis/LODGaZilla-picoscope_csv_importer.git "HOST/plugin/picoscope_csv_importer"
python "HOST/tools/setup_environment.py"
```

Enable it in the application's **Settings > Plugins**. `requirements.txt` declares
direct dependencies; `constraints.txt` records versions tested on Windows/Python 3.11.
The application and plugin requirements are resolved together in the Python
installation running the setup helper; no virtual environment is created.
The application can run without this plugin. Update this checkout
explicitly with `git pull --ff-only`, then rerun dependency setup in the host.
Plugin changes are committed and pushed here, not in the application repository.

Plugin settings, where present, load committed `settings.json` defaults and save
user changes to ignored `settings.local.json`. Keep captured input files, logs
and personal settings out of the repository.

## Tests

From any independent plugin checkout, point the host's runner at this directory:

```powershell
python "HOST/tools/run_tests.py" --host "HOST" --plugin "." --isolated
```

Use the same Python installation for dependency setup, the application and tests.
The runner stages this plugin under its stable ID and
supplies the host path; tests never rely on sibling plugin repositories. Integration
tests use the real host API, with synthetic data and no connected hardware.
Add `--all-plugins` to validate alongside other plugins installed in HOST.

## License

Copyright (C) 2026 Riku Tervonen

This plugin is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License as published by the Free Software Foundation, either
version 3 of the License, or (at your option) any later version. It is distributed in
the hope that it will be useful, but without any warranty; without even the implied
warranty of merchantability or fitness for a particular purpose. See [LICENSE](LICENSE)
for the full text (SPDX: `GPL-3.0-or-later`). Its dependencies keep their own licenses.
