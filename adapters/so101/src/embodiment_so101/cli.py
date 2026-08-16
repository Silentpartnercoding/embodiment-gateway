"""Standalone SO-101 embodiment commands; no Invention Graph imports."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from embodiment_gateway import (
    EmbodimentPlan,
    EmbodimentReceiptLog,
    NamedPose,
    RootJudgment,
    RootJudgmentEmbodimentGateway,
    SandboxAuthorization,
    canonical_digest,
)

from .adapter import MockSO101Adapter, SO101LeRobotAdapter, SO101_ACTION_KEYS


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="embodiment-so101")
    commands = parser.add_subparsers(dest="command", required=True)

    demo = commands.add_parser("demo", help="run a hardware-free contract heartbeat")
    demo.add_argument("--run-id", default="embodiment-demo-1")
    demo.add_argument("--receipt-log", type=Path, default=Path("data/embodiment-receipts.jsonl"))
    demo.add_argument("--receipt-output", type=Path)

    wrap = commands.add_parser(
        "wrap-judgment",
        help="wrap a provider-neutral root verdict as a digest-bound handoff",
    )
    wrap.add_argument("verdict", type=Path)
    wrap.add_argument("--subject-id", required=True)
    wrap.add_argument("--issuer", required=True)
    wrap.add_argument("--output", type=Path, required=True)

    run = commands.add_parser("run", help="execute files bound to one external judgment")
    run.add_argument("judgment", type=Path)
    run.add_argument("plan", type=Path)
    run.add_argument("sandbox", type=Path)
    run.add_argument("--adapter", choices=("mock", "so101"), default="mock")
    run.add_argument("--port")
    run.add_argument("--robot-id")
    run.add_argument("--calibration-id")
    run.add_argument("--execute-physical", action="store_true")
    run.add_argument("--receipt-log", type=Path, default=Path("data/embodiment-receipts.jsonl"))
    run.add_argument("--receipt-output", type=Path)

    verify = commands.add_parser("verify", help="verify an intent/completion hash chain")
    verify.add_argument("receipt_log", type=Path)

    inspect = commands.add_parser("inspect", help="inspect calibration and pose without motion")
    inspect.add_argument("--port", required=True)
    inspect.add_argument("--robot-id", required=True)
    inspect.add_argument("--connect-hardware", action="store_true")
    return parser


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _demo_fixture(run_id: str):
    judgment = RootJudgment(
        subject_id="hypothesis:embodiment-heartbeat",
        issuer="fixture:root-judge",
        verdict="true",
        support_roots=("fixture:root:1", "fixture:root:2"),
        challenge_roots=(),
        flip_budget=2,
        conflicting_roots=(),
        unattributed_claims=0,
        issued_at="2026-08-15T00:00:00+00:00",
    )
    robot_id = "so101-mock"
    calibration_id = "mock-calibration-v1"
    sandbox = SandboxAuthorization(
        authorization_id="sandbox:so101-mock-v1",
        robot_family="so101",
        robot_id=robot_id,
        calibration_id=calibration_id,
        poses=(
            NamedPose("home", {key: 0.0 for key in SO101_ACTION_KEYS}),
            NamedPose("signal", {
                "shoulder_pan.pos": 8.0,
                "shoulder_lift.pos": -6.0,
                "elbow_flex.pos": 5.0,
                "wrist_flex.pos": 0.0,
                "wrist_roll.pos": 0.0,
                "gripper.pos": 10.0,
            }),
        ),
        allowed_transitions=(("START", "home"), ("home", "signal"), ("signal", "home")),
        max_pose_actions=3,
        max_motor_commands=20,
        max_normalized_delta_per_command=5.0,
        pose_tolerance=1.0,
        settle_timeout_seconds=1.0,
        poll_interval_seconds=0.01,
        approved_by="fixture:operator",
        approval_receipt_digest=canonical_digest({"scope": "mock-only"}),
        armed=True,
        workspace_cleared=True,
        external_estop_attested=False,
        physical_execution_authorized=False,
    )
    plan = EmbodimentPlan(
        run_id=run_id,
        hypothesis_id=judgment.subject_id,
        experiment_id="experiment:embodiment-heartbeat",
        judgment_digest=judgment.digest,
        sandbox_digest=sandbox.digest,
        pose_sequence=("home", "signal", "home"),
        expected_final_pose="home",
        purpose="hardware-free gateway heartbeat",
    )
    return judgment, plan, sandbox, MockSO101Adapter(
        robot_id=robot_id,
        calibration_id=calibration_id,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "wrap-judgment":
        judgment = RootJudgment.from_root_verdict(
            subject_id=args.subject_id,
            issuer=args.issuer,
            verdict_payload=_load(args.verdict),
            issued_at=datetime.now(timezone.utc).isoformat(),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(judgment.payload(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        result = {"written": str(args.output), "judgment_digest": judgment.digest}
    elif args.command == "verify":
        result = EmbodimentReceiptLog(args.receipt_log).verify()
    elif args.command == "inspect":
        if not args.connect_hardware:
            raise ValueError("hardware inspection requires --connect-hardware")
        adapter = SO101LeRobotAdapter(
            port=args.port,
            robot_id=args.robot_id,
            calibration_id="discover",
            physical_execution_enabled=False,
        )
        try:
            adapter.connect()
            result = {
                "adapter_manifest": adapter.manifest.payload(),
                "adapter_manifest_digest": adapter.manifest.digest,
                "observation": dict(adapter.observe()),
                "motion_commanded": False,
            }
        finally:
            adapter.disconnect()
    else:
        if args.command == "demo":
            judgment, plan, sandbox, adapter = _demo_fixture(args.run_id)
        else:
            judgment = RootJudgment.parse(_load(args.judgment))
            plan = EmbodimentPlan.parse(_load(args.plan))
            sandbox = SandboxAuthorization.parse(_load(args.sandbox))
            if args.adapter == "so101":
                if not args.port:
                    raise ValueError("--port is required for the real SO-101 adapter")
                adapter = SO101LeRobotAdapter(
                    port=args.port,
                    robot_id=args.robot_id or sandbox.robot_id,
                    calibration_id=args.calibration_id or sandbox.calibration_id,
                    physical_execution_enabled=args.execute_physical,
                )
            else:
                adapter = MockSO101Adapter(
                    robot_id=args.robot_id or sandbox.robot_id,
                    calibration_id=args.calibration_id or sandbox.calibration_id,
                )
        journal = EmbodimentReceiptLog(args.receipt_log)
        receipt = RootJudgmentEmbodimentGateway().run(
            judgment,
            plan,
            sandbox,
            adapter,
            journal=journal,
        )
        if args.receipt_output:
            args.receipt_output.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.receipt_output.with_suffix(args.receipt_output.suffix + ".tmp")
            temporary.write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            temporary.replace(args.receipt_output)
        result = {**receipt, "journal": journal.verify()}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
