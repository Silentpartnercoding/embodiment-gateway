# Architecture, ELI5

Embodiment Gateway is the body's **spinal cord and safety envelope**. It does
not decide what is scientifically true. It accepts a sealed judgment from an
outside judge, verifies that the requested movement fits a human-approved box,
and asks one adapter to carry it out.

## Components

1. **Root judgment** — the sealed note saying what was judged, which independent
   evidence roots supported it, and how hard the conclusion is to reverse.
2. **Plan** — the requested sequence of already named poses. It contains the
   exact judgment and sandbox digests, so neither can be swapped later.
3. **Sandbox authorization** — the human-defined playpen: exact device,
   calibration, poses, legal transitions, motion budget, cleared workspace, and
   emergency-stop attestations.
4. **Gateway core** — checks the judgment threshold and every sandbox boundary;
   a pass must execute, while a reject or defer cannot connect to the adapter.
5. **Adapter protocol** — the tiny socket any body must implement: connect,
   observe, send a bounded target, halt, and disconnect.
6. **SO-101 adapter** — translates that socket into LeRobot/Feetech operations
   for the inexpensive physical arm. It alone knows ports and motor calibration.
7. **Berkeley Humanoid Lite adapter** — translates the same socket into a
   digest-pinned whole-body simulation. It is always marked `hardware=false`.
8. **Receipt log** — writes intent before execution and completion afterward in
   a hash chain. A crash after intent is never automatically replayed.

## Authority boundary

The Gateway cannot invent evidence, approve a new pose, widen a joint range, or
turn a simulation receipt into physical authority. A receipt proves that the
declared adapter reached an approved target; it does not prove scientific truth
or real-world task success.

## Relationship to Invention Graph

Invention Graph is a separate research engine. It can export a root-verdict JSON
and later ingest a reviewed result receipt. The handoff is data over a documented
contract—not a Python import, shared database, or combined release.
