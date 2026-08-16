from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from embodiment_gateway import (
    EmbodimentAdapterManifest,
    EmbodimentPlan,
    EmbodimentReceiptLog,
    EmbodimentReplayError,
    MemoryAdapter,
    NamedPose,
    RootJudgment,
    RootJudgmentEmbodimentGateway,
    SandboxAuthorization,
    canonical_digest,
)


def fixture(*, roots: int = 2, run_id: str = "run-1"):
    judgment = RootJudgment(
        subject_id="hypothesis:demo",
        issuer="minority-prophet:test",
        verdict="true",
        support_roots=tuple(f"root:{index}" for index in range(roots)),
        challenge_roots=(),
        flip_budget=roots,
        conflicting_roots=(),
        unattributed_claims=0,
        issued_at="2026-08-15T00:00:00+00:00",
    )
    sandbox = SandboxAuthorization(
        authorization_id="sandbox:test",
        robot_family="test-arm",
        robot_id="arm-1",
        calibration_id="calibration-1",
        poses=(
            NamedPose("home", {"axis-a": 0.0, "axis-b": 0.0}),
            NamedPose("signal", {"axis-a": 8.0, "axis-b": -6.0}),
        ),
        allowed_transitions=(("START", "home"), ("home", "signal"), ("signal", "home")),
        max_pose_actions=3,
        max_motor_commands=20,
        max_normalized_delta_per_command=5.0,
        pose_tolerance=1.0,
        settle_timeout_seconds=1.0,
        poll_interval_seconds=0.01,
        approved_by="operator:test",
        approval_receipt_digest=canonical_digest({"approved": "sandbox:test"}),
        armed=True,
        workspace_cleared=True,
        external_estop_attested=False,
        physical_execution_authorized=False,
    )
    plan = EmbodimentPlan(
        run_id=run_id,
        hypothesis_id=judgment.subject_id,
        judgment_digest=judgment.digest,
        sandbox_digest=sandbox.digest,
        pose_sequence=("home", "signal", "home"),
        expected_final_pose="home",
    )
    adapter = MemoryAdapter(
        EmbodimentAdapterManifest(
            adapter_id="memory:test",
            adapter_version="1.0.0",
            robot_family=sandbox.robot_family,
            robot_id=sandbox.robot_id,
            calibration_id=sandbox.calibration_id,
            hardware=False,
            transport="in-process",
        ),
        {"axis-a": 0.0, "axis-b": 0.0},
    )
    return judgment, sandbox, plan, adapter


class GatewayTest(unittest.TestCase):
    def test_pass_mandatorily_executes_and_hashes_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            judgment, sandbox, plan, adapter = fixture()
            journal = EmbodimentReceiptLog(Path(directory) / "receipts.jsonl")
            gateway = RootJudgmentEmbodimentGateway()
            receipt = gateway.run(judgment, plan, sandbox, adapter, journal=journal)

            self.assertEqual(receipt["status"], "completed")
            self.assertEqual(receipt["decision"]["disposition"], "pass")
            self.assertTrue(receipt["mandatory_action_taken"])
            self.assertTrue(receipt["final_pose_verified"])
            self.assertFalse(receipt["scientific_claim"])
            self.assertFalse(receipt["evidence_root_minted"])
            self.assertGreater(len(adapter.commands), len(plan.pose_sequence))
            self.assertTrue(receipt["receipt_digest"].startswith("sha256:"))
            self.assertEqual(journal.verify()["entries"], 2)

            replay = gateway.run(judgment, plan, sandbox, adapter, journal=journal)
            self.assertEqual(replay["receipt_digest"], receipt["receipt_digest"])
            self.assertEqual(journal.verify()["entries"], 2)

    def test_thin_external_judgment_defers_without_connecting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            judgment, sandbox, plan, adapter = fixture(roots=1)
            journal = EmbodimentReceiptLog(Path(directory) / "receipts.jsonl")
            receipt = RootJudgmentEmbodimentGateway().run(
                judgment, plan, sandbox, adapter, journal=journal
            )
            self.assertEqual(receipt["decision"]["disposition"], "defer")
            self.assertIn(
                "insufficient_independent_support_roots",
                receipt["decision"]["reasons"],
            )
            self.assertFalse(adapter.connected)
            self.assertEqual(adapter.commands, [])
            self.assertEqual(journal.entries(), [])

    def test_plan_is_bound_to_exact_external_judgment(self) -> None:
        judgment, sandbox, plan, adapter = fixture()
        changed = replace(judgment, issuer="different-judge")
        decision = RootJudgmentEmbodimentGateway().judge(changed, plan, sandbox, adapter)
        self.assertEqual(decision["disposition"], "defer")
        self.assertIn("judgment_digest_mismatch", decision["reasons"])

    def test_unapproved_transition_fails_closed(self) -> None:
        judgment, sandbox, plan, adapter = fixture()
        plan = replace(plan, pose_sequence=("signal",), expected_final_pose="signal")
        decision = RootJudgmentEmbodimentGateway().judge(judgment, plan, sandbox, adapter)
        self.assertEqual(decision["disposition"], "defer")
        self.assertIn("transition_not_authorized", decision["reasons"])

    def test_dangling_intent_is_never_replayed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            judgment, sandbox, plan, adapter = fixture()
            journal = EmbodimentReceiptLog(Path(directory) / "receipts.jsonl")
            journal.begin(plan, sandbox)
            with self.assertRaises(EmbodimentReplayError):
                RootJudgmentEmbodimentGateway().run(
                    judgment, plan, sandbox, adapter, journal=journal
                )
            self.assertEqual(adapter.commands, [])

    def test_hardware_requires_separate_physical_attestations(self) -> None:
        judgment, sandbox, plan, adapter = fixture()
        adapter.manifest = replace(adapter.manifest, hardware=True, transport="serial:test")
        decision = RootJudgmentEmbodimentGateway().judge(judgment, plan, sandbox, adapter)
        self.assertEqual(decision["disposition"], "defer")
        self.assertIn("physical_execution_not_authorized", decision["reasons"])
        self.assertIn("external_estop_not_attested", decision["reasons"])

    def test_wraps_provider_neutral_root_verdict_payload(self) -> None:
        judgment = RootJudgment.from_root_verdict(
            subject_id="hypothesis:portable",
            issuer="minority-prophet:local",
            issued_at="2026-08-15T00:00:00+00:00",
            verdict_payload={
                "verdict": "true",
                "support_roots": ["root:a", "root:b"],
                "challenge_roots": [],
                "flip_budget": 2,
                "conflicting_roots": [],
                "unattributed_claims": 0,
                "extra_producer_field": "ignored",
            },
        )
        self.assertEqual(judgment.verdict, "true")
        self.assertEqual(judgment.support_roots, ("root:a", "root:b"))


if __name__ == "__main__":
    unittest.main()
