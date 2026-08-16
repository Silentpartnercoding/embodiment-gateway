# Embodiment SO-101

This independently installable plugin is the only package that knows about
LeRobot, Feetech motors, SO-101 channels, serial ports, or calibration records.
It implements the adapter protocol from the Gateway core in this repository.

It remains its own Python distribution, but it is developed and released from
the Embodiment Gateway repository. Run these commands from the repository root:

```bash
.venv/bin/pip install -e .
.venv/bin/pip install -e adapters/so101
.venv/bin/python -m unittest discover -s adapters/so101/tests -p 'test_*.py' -v
```

Hardware support is installed only with `embodiment-so101[hardware]`.
