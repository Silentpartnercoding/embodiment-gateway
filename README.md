# Embodiment Gateway

This is an independently installable, hardware-neutral execution boundary. It
accepts a digest-bound root judgment, a human-approved sandbox, an execution
plan bound to both, and a device adapter. It returns a hash-bound receipt.

It does not import Invention Graph, Minority Prophet, LeRobot, or any hardware
SDK. A passing judgment cannot expand the human-authorized sandbox, and a
dangling physical intent is never replayed automatically.

This is its own project and distribution. It is not part of the
[`invention-graph`](https://github.com/Silentpartnercoding/invention-graph)
repository or wheel.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```
