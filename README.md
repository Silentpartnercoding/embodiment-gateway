# Embodiment Gateway

One repository for the complete Embodiment system:

| Component | Distribution | Responsibility |
| --- | --- | --- |
| Gateway core | `embodiment-gateway` | Judgment binding, human sandbox, execution limits, and receipts |
| SO-101 adapter | `embodiment-so101` | LeRobot, Feetech motors, calibration, ports, and physical joint commands |
| Humanoid simulation adapter | `embodiment-berkeley-humanoid-lite` | Digest-pinned Berkeley Humanoid Lite simulation |

The core is hardware-neutral. It does not import Invention Graph, Minority
Prophet, LeRobot, or a simulator. Adapters implement its small execution
protocol and cannot expand the approved sandbox.

```text
external root judgment + human sandbox
                  ↓
         Embodiment Gateway
                  ↓
       ┌──────────┴──────────┐
       ↓                     ↓
  SO-101 adapter       Humanoid simulator
       └──────────┬──────────┘
                  ↓
       hash-bound result receipt
```

This repository is independent of
[`invention-graph`](https://github.com/Silentpartnercoding/invention-graph).
The projects exchange JSON and digests; neither imports the other.

The canonical cross-project schemas ship inside the core wheel under
`embodiment_gateway/contracts/`. The Gateway owns those schemas because it is
the security boundary consuming judgments and producing receipts. Invention
Graph independently verifies the same digest bindings before recording a
returned receipt.

```bash
embodiment-gateway contracts --output-dir /tmp/embodiment-contracts
embodiment-gateway wrap-judgment verdict.json \
  --subject-id HYPOTHESIS_ID --issuer root-judge:local \
  --output judgment.json
embodiment-gateway verify-receipt receipt.json
```

A valid receipt proves internal integrity and exact object binding, not signer
identity or scientific truth. Every v1 receipt—including a rejected or deferred
run—is digest-sealed and retains `scientific_claim=false`,
`evidence_root_minted=false`, and `authority_scope=embodiment_health_only`.

## Install and test

From this repository's root:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pip install -e adapters/so101
.venv/bin/pip install -e adapters/berkeley-humanoid-lite

.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
.venv/bin/python -m unittest discover -s adapters/so101/tests -p 'test_*.py' -v
.venv/bin/python -m unittest discover \
  -s adapters/berkeley-humanoid-lite/tests -p 'test_*.py' -v
```

Hardware support is always explicit:

```bash
.venv/bin/pip install -e 'adapters/so101[hardware]'
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for authority boundaries and
the ELI5 component map.
