"""Small simulation-only CLI for the Berkeley Humanoid Lite adapter."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from embodiment_gateway import (
    EmbodimentPlan,
    EmbodimentReceiptLog,
    NamedPose,
    RootJudgment,
    RootJudgmentEmbodimentGateway,
    SandboxAuthorization,
    canonical_digest,
)

from .adapter import (
    BerkeleyHumanoidLiteSimulationAdapter,
    HumanoidLiteSimulationProfile,
    MockHumanoidLiteBackend,
)


DEMO_CHANNELS = ("left_hip_pitch", "right_hip_pitch", "left_knee", "right_knee")


def _demo(receipt_log: Path) -> dict:
    limits = {channel: (-1.0, 1.0) for channel in DEMO_CHANNELS}
    profile = HumanoidLiteSimulationProfile(
        source_revision="mock-fixture-v1",
        backend="mock",
        model_variant="humanoid-lite-ci",
        action_channels=DEMO_CHANNELS,
        joint_limits=limits,
        upstream_config_digest=canonical_digest({"config": "ci-v1"}),
        upstream_assets_revision="mock-assets-v1",
        upstream_lowlevel_revision="mock-lowlevel-v1",
    )
    initial = {channel: 0.0 for channel in DEMO_CHANNELS}
    adapter = BerkeleyHumanoidLiteSimulationAdapter(
        profile=profile,
        backend=MockHumanoidLiteBackend(initial),
    )
    judgment = RootJudgment(
        subject_id="hypothesis:humanoid-simulation-heartbeat",
        issuer="minority-prophet:fixture",
        verdict="true",
        support_roots=("root:one", "root:two"),
        challenge_roots=(),
        flip_budget=2,
        conflicting_roots=(),
        unattributed_claims=0,
        issued_at="2026-08-15T00:00:00+00:00",
    )
    signal = dict(initial)
    signal["left_hip_pitch"] = 0.15
    signal["right_hip_pitch"] = -0.15
    sandbox = SandboxAuthorization(
        authorization_id="sandbox:humanoid-lite-simulation-demo",
        robot_family=adapter.manifest.robot_family,
        robot_id=adapter.manifest.robot_id,
        calibration_id=profile.digest,
        poses=(NamedPose("home", initial), NamedPose("signal", signal)),
        allowed_transitions=(
            ("START", "home"),
            ("home", "signal"),
            ("signal", "home"),
        ),
        max_pose_actions=3,
        max_motor_commands=20,
        max_normalized_delta_per_command=0.1,
        pose_tolerance=0.1,
        settle_timeout_seconds=1.0,
        poll_interval_seconds=0.01,
        approved_by="operator:fixture",
        approval_receipt_digest=canonical_digest({"approved": "demo"}),
        armed=True,
        workspace_cleared=True,
        external_estop_attested=False,
        physical_execution_authorized=False,
    )
    plan = EmbodimentPlan(
        run_id="humanoid-lite-simulation-demo",
        hypothesis_id=judgment.subject_id,
        judgment_digest=judgment.digest,
        sandbox_digest=sandbox.digest,
        pose_sequence=("home", "signal", "home"),
        expected_final_pose="home",
        purpose="simulation-only whole-body adapter heartbeat",
    )
    return RootJudgmentEmbodimentGateway().run(
        judgment,
        plan,
        sandbox,
        adapter,
        journal=EmbodimentReceiptLog(receipt_log),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="embodiment-humanoid-lite")
    subparsers = parser.add_subparsers(dest="command", required=True)
    demo = subparsers.add_parser("demo")
    demo.add_argument(
        "--receipt-log",
        type=Path,
        default=Path("humanoid-lite-simulation-receipts.jsonl"),
    )
    args = parser.parse_args(argv)
    if args.command == "demo":
        result = _demo(args.receipt_log)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] == "completed" else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
