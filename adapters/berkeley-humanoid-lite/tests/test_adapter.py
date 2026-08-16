from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from embodiment_gateway import (
    EmbodimentContractError,
    EmbodimentPlan,
    EmbodimentReceiptLog,
    NamedPose,
    RootJudgment,
    RootJudgmentEmbodimentGateway,
    SandboxAuthorization,
    canonical_digest,
)
from embodiment_berkeley_humanoid_lite import (
    BerkeleyHumanoidLiteSimulationAdapter,
    HumanoidLiteSimulationProfile,
    MockHumanoidLiteBackend,
    OfficialMujocoBackend,
    PINNED_HUMANOID_ACTION_CHANNELS,
    pinned_mujoco_humanoid_profile,
)
from embodiment_berkeley_humanoid_lite.cli import main


CHANNELS = ("left_hip_pitch", "right_hip_pitch", "left_knee", "right_knee")


def profile(**changes) -> HumanoidLiteSimulationProfile:
    values = {
        "source_revision": "984741a3623c93b0583ccfdc479f1f8b1c4d900e",
        "backend": "mock",
        "model_variant": "humanoid-lite-v1.1",
        "action_channels": CHANNELS,
        "joint_limits": {channel: (-1.0, 1.0) for channel in CHANNELS},
        "upstream_config_digest": canonical_digest({"cfg": "test"}),
        "upstream_assets_revision": "mock-assets-v1",
        "upstream_lowlevel_revision": "mock-lowlevel-v1",
    }
    values.update(changes)
    return HumanoidLiteSimulationProfile(**values)


def fixture():
    bound_profile = profile()
    initial = {channel: 0.0 for channel in CHANNELS}
    adapter = BerkeleyHumanoidLiteSimulationAdapter(
        profile=bound_profile,
        backend=MockHumanoidLiteBackend(initial),
    )
    judgment = RootJudgment(
        subject_id="hypothesis:whole-body-heartbeat",
        issuer="minority-prophet:test",
        verdict="true",
        support_roots=("root:a", "root:b"),
        challenge_roots=(),
        flip_budget=2,
        conflicting_roots=(),
        unattributed_claims=0,
        issued_at="2026-08-15T00:00:00+00:00",
    )
    signal = dict(initial)
    signal["left_hip_pitch"] = 0.2
    signal["right_hip_pitch"] = -0.2
    sandbox = SandboxAuthorization(
        authorization_id="sandbox:humanoid-test",
        robot_family=adapter.manifest.robot_family,
        robot_id=adapter.manifest.robot_id,
        calibration_id=bound_profile.digest,
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
        settle_timeout_seconds=0.2,
        poll_interval_seconds=0.01,
        approved_by="operator:test",
        approval_receipt_digest=canonical_digest({"approved": True}),
        armed=True,
        workspace_cleared=True,
        external_estop_attested=False,
        physical_execution_authorized=False,
    )
    plan = EmbodimentPlan(
        run_id="run:humanoid-test",
        hypothesis_id=judgment.subject_id,
        judgment_digest=judgment.digest,
        sandbox_digest=sandbox.digest,
        pose_sequence=("home", "signal", "home"),
        expected_final_pose="home",
        purpose="simulation adapter conformance",
    )
    return bound_profile, adapter, judgment, sandbox, plan


class HumanoidLiteSimulationAdapterTest(unittest.TestCase):
    def test_gateway_executes_but_never_claims_physical_or_scientific_proof(self) -> None:
        bound_profile, adapter, judgment, sandbox, plan = fixture()
        with tempfile.TemporaryDirectory() as directory:
            receipt = RootJudgmentEmbodimentGateway().run(
                judgment,
                plan,
                sandbox,
                adapter,
                journal=EmbodimentReceiptLog(Path(directory) / "receipts.jsonl"),
            )
        self.assertEqual(receipt["status"], "completed")
        self.assertFalse(receipt["adapter_manifest"]["hardware"])
        self.assertEqual(receipt["adapter_manifest"]["calibration_id"], bound_profile.digest)
        self.assertFalse(receipt["decision"]["physical_execution_authorized"])
        self.assertFalse(receipt["scientific_claim"])
        self.assertFalse(receipt["evidence_root_minted"])

    def test_changed_upstream_revision_cannot_reuse_approved_sandbox(self) -> None:
        _, _, judgment, sandbox, plan = fixture()
        changed_profile = profile(source_revision="different-upstream-revision")
        adapter = BerkeleyHumanoidLiteSimulationAdapter(
            profile=changed_profile,
            backend=MockHumanoidLiteBackend({channel: 0.0 for channel in CHANNELS}),
        )
        decision = RootJudgmentEmbodimentGateway().judge(
            judgment, plan, sandbox, adapter
        )
        self.assertEqual(decision["disposition"], "defer")
        self.assertIn("calibration_id_mismatch", decision["reasons"])

    def test_profile_rejects_missing_channels_and_out_of_range_targets(self) -> None:
        bound_profile = profile()
        with self.assertRaises(EmbodimentContractError):
            bound_profile.values({CHANNELS[0]: 0.0}, label="target")
        invalid = {channel: 0.0 for channel in CHANNELS}
        invalid[CHANNELS[0]] = 2.0
        with self.assertRaises(EmbodimentContractError):
            bound_profile.values(invalid, label="target")

    def test_profile_digest_binds_joint_limits_config_and_assets(self) -> None:
        original = profile()
        wider_limits = {channel: (-2.0, 2.0) for channel in CHANNELS}
        self.assertNotEqual(
            original.digest,
            profile(joint_limits=wider_limits).digest,
        )
        self.assertNotEqual(
            original.digest,
            profile(upstream_config_digest=canonical_digest({"cfg": "changed"})).digest,
        )
        self.assertNotEqual(
            original.digest,
            profile(upstream_assets_revision="mock-assets-changed").digest,
        )

    def test_pinned_profile_matches_the_inspected_22_channel_contract(self) -> None:
        limits = {
            channel: (-1.0, 1.0) for channel in PINNED_HUMANOID_ACTION_CHANNELS
        }
        bound = pinned_mujoco_humanoid_profile(joint_limits=limits)
        self.assertEqual(bound.backend, "mujoco")
        self.assertEqual(len(bound.action_channels), 22)
        self.assertEqual(bound.action_channels, PINNED_HUMANOID_ACTION_CHANNELS)

    def test_declared_backend_must_match_the_runtime_backend(self) -> None:
        with self.assertRaises(EmbodimentContractError):
            BerkeleyHumanoidLiteSimulationAdapter(
                profile=profile(backend="mujoco"),
                backend=MockHumanoidLiteBackend(
                    {channel: 0.0 for channel in CHANNELS}
                ),
            )

    def test_official_mujoco_bridge_matches_documented_observation_layout(self) -> None:
        class FakeCfg:
            action_indices = tuple(range(len(CHANNELS)))

        class FakeSimulator:
            def __init__(self, cfg):
                self.cfg = cfg
                self.received = None

            def reset(self):
                return [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.1, 0.2, 0.3, 0.4]

            def step(self, actions):
                self.received = list(actions)
                return [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, *self.received]

        upstream = types.ModuleType("berkeley_humanoid_lite")
        environments = types.ModuleType("berkeley_humanoid_lite.environments")
        environments.MujocoSimulator = FakeSimulator
        torch = types.ModuleType("torch")
        torch.float32 = "float32"
        torch.tensor = lambda values, dtype=None: list(values)
        with patch.dict(
            sys.modules,
            {
                "berkeley_humanoid_lite": upstream,
                "berkeley_humanoid_lite.environments": environments,
                "torch": torch,
            },
        ):
            backend = OfficialMujocoBackend(cfg=FakeCfg(), action_channels=CHANNELS)
            backend.connect()
            self.assertEqual(backend.observe()["left_hip_pitch"], 0.1)
            target = {channel: index / 10 for index, channel in enumerate(CHANNELS)}
            self.assertEqual(backend.step(target), target)
            backend.disconnect()

    def test_cli_demo_produces_a_hash_bound_simulation_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            receipt_log = Path(directory) / "receipts.jsonl"
            receipt_output = Path(directory) / "outbox" / "receipt.json"
            with redirect_stdout(StringIO()):
                self.assertEqual(main([
                    "demo",
                    "--receipt-log", str(receipt_log),
                    "--receipt-output", str(receipt_output),
                ]), 0)
            rows = EmbodimentReceiptLog(receipt_log).entries()
            self.assertEqual(rows[-1]["receipt"]["status"], "completed")
            self.assertFalse(rows[-1]["receipt"]["adapter_manifest"]["hardware"])
            self.assertEqual(
                json.loads(receipt_output.read_text(encoding="utf-8"))["receipt_digest"],
                rows[-1]["receipt"]["receipt_digest"],
            )


if __name__ == "__main__":
    unittest.main()
