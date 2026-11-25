# Contributing

Thanks for considering a contribution. This is a small community tool, kept
intentionally simple -- please keep that in mind when proposing changes.

## Before you start

- For anything beyond a small fix, open an issue first describing the
  problem and your proposed approach, so we can agree on direction before
  you invest time.
- Changes that touch `faikout_config/safety.py`, or anything in the
  firmware-update-protection path (`assert_does_not_enable_ota_auto` and its
  callers in `sync.py`/`profiles.py`/`restore.py`), need an explicit
  justification in the PR description and will get the most scrutiny. This
  guard exists specifically so that no combination of sync/restore/profile
  features can ever re-enable automatic firmware updates as a side effect.
- Please don't add new runtime dependencies without discussing it in an
  issue first -- this project deliberately has exactly two (`flask`,
  `paho-mqtt`).

## Development setup

```bash
git clone <this repo>
cd faikout-config-manager
python3 -m venv .venv
source .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

All 100+ tests should pass offline -- the test suite includes a hand-written,
dependency-free MQTT broker (`tests/fake_mqtt_broker.py`) plus simulated
controllers (`tests/simulated_controller.py`), so you don't need a real
broker or real hardware to develop or test against.

## Pull requests

- Add or update tests for any behavior change. If you're fixing a bug, add a
  test that fails before your fix and passes after.
- Keep the change scoped to what the issue/PR describes -- avoid bundling
  unrelated refactors.
- CI (`.github/workflows/ci.yml`) runs the full test suite on Python 3.10,
  3.11, and 3.12, and builds the Docker image. Please make sure it's green
  before requesting review.
- Describe what you tested manually, if anything, beyond the automated suite
  (e.g. "tested against a real Faikout controller on firmware X").

## Code style

- No comments explaining *what* code does -- only *why*, when it's genuinely
  non-obvious (a hidden constraint, a workaround, a subtle invariant).
- Prefer small, focused functions/modules over large ones. The existing
  `faikout_config/` package is organized by concern (one file per concept:
  planes, matrix, diff, sync, backup, restore, profiles, safety, audit,
  registry, discovery, protocol) -- new code should generally fit into one
  of these, not create a new catch-all module.
