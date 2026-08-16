# Berkeley Humanoid Lite simulation adapter

This independently installable package connects the hardware-neutral
Gateway core contract in this repository to a pinned Berkeley Humanoid Lite
simulation.
It is a harder whole-body simulation surface beside the SO-101 physical
baseline; it does not replace that baseline and cannot authorize hardware.

The package does not vendor Berkeley Humanoid Lite code, models, policies, or
assets. Install and license the upstream simulation stack separately. The
adapter binds its repository revision, backend, model variant, action channels,
joint limits, upstream configuration digest, and asset/low-level submodule
revisions into one simulation-profile digest. That digest occupies the
gateway's `calibration_id` slot, so a changed simulation cannot silently reuse
an approved sandbox.

`MockHumanoidLiteBackend` provides deterministic CI coverage.
`OfficialMujocoBackend` is a lazy bridge to the upstream `MujocoSimulator` and
requires the upstream repository and its submodules to be installed according
to their documentation.

```bash
.venv/bin/pip install -e .
.venv/bin/pip install -e adapters/berkeley-humanoid-lite

.venv/bin/python -m unittest discover \
  -s adapters/berkeley-humanoid-lite/tests -p 'test_*.py' -v

.venv/bin/python -m embodiment_berkeley_humanoid_lite demo \
  --receipt-log /tmp/humanoid-lite-simulation-receipts.jsonl
```

The completion receipt proves only that a digest-bound simulation accepted and
verified the approved joint targets. It does not prove physical safety,
sim-to-real transfer, task success in the world, or a scientific claim.

It remains its own Python distribution but is developed and released from the
Embodiment Gateway repository. It is not part of the `invention-graph` wheel.
