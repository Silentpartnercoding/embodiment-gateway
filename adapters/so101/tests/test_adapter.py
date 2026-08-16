from __future__ import annotations

import types
import unittest
import json
import tempfile
from pathlib import Path
from dataclasses import dataclass
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from embodiment_gateway import (
    EmbodimentContractError,
    RootJudgment,
    verify_embodiment_receipt,
)
from embodiment_so101 import (
    SO101_ACTION_KEYS,
    SO101LeRobotAdapter,
    lerobot_calibration_digest,
    so101_values,
)
from embodiment_so101.cli import main


class SO101AdapterTest(unittest.TestCase):
    def test_demo_atomically_exports_a_portable_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "outbox" / "heartbeat.receipt.json"
            with redirect_stdout(StringIO()):
                self.assertEqual(
                    main([
                        "demo",
                        "--receipt-log", str(root / "receipts.jsonl"),
                        "--receipt-output", str(output),
                    ]),
                    0,
                )
            receipt = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(verify_embodiment_receipt(receipt)["valid"])
            self.assertFalse(output.with_suffix(output.suffix + ".tmp").exists())

    def test_cli_wraps_root_verdict_without_importing_invention_engine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            verdict = root / "verdict.json"
            output = root / "judgment.json"
            verdict.write_text(json.dumps({
                "verdict": "true",
                "support_roots": ["root:a", "root:b"],
                "challenge_roots": [],
                "flip_budget": 2,
                "conflicting_roots": [],
                "unattributed_claims": 0,
            }))
            self.assertEqual(main([
                "wrap-judgment",
                str(verdict),
                "--subject-id", "hypothesis:test",
                "--issuer", "minority-prophet:test",
                "--output", str(output),
            ]), 0)
            judgment = RootJudgment.parse(json.loads(output.read_text()))
            self.assertEqual(judgment.subject_id, "hypothesis:test")

    def test_real_adapter_requires_digest_bound_calibration(self) -> None:
        with self.assertRaises(EmbodimentContractError):
            SO101LeRobotAdapter(
                port="/dev/tty.usbmodem-test",
                robot_id="so101-follower",
                calibration_id="calibration-v1",
            )

    def test_normalized_limits_live_in_plugin_not_gateway(self) -> None:
        target = {key: 0.0 for key in SO101_ACTION_KEYS}
        target["gripper.pos"] = -1.0
        with self.assertRaises(EmbodimentContractError):
            so101_values(target, label="target")

    def test_matches_current_lerobot_python_contract(self) -> None:
        @dataclass
        class Calibration:
            id: int
            drive_mode: int
            homing_offset: int
            range_min: int
            range_max: int

        calibration = {
            key.removesuffix(".pos"): Calibration(index + 1, 0, index, -100, 100)
            for index, key in enumerate(SO101_ACTION_KEYS)
        }

        class FakeConfig:
            def __init__(self, **values):
                self.values = values

        class FakeRobot:
            def __init__(self, config):
                self.config = config
                self.is_calibrated = True
                self.calibration = calibration
                self.state = {f"{name}.pos": 0.0 for name in calibration}

            def connect(self, calibrate=True):
                self.connect_calibrate = calibrate

            def get_observation(self):
                return dict(self.state)

            def send_action(self, action):
                self.state = dict(action)
                return dict(action)

            def disconnect(self):
                return None

        lerobot = types.ModuleType("lerobot")
        robots = types.ModuleType("lerobot.robots")
        so_follower = types.ModuleType("lerobot.robots.so_follower")
        so_follower.SO101Follower = FakeRobot
        so_follower.SO101FollowerConfig = FakeConfig
        with patch.dict(
            "sys.modules",
            {
                "lerobot": lerobot,
                "lerobot.robots": robots,
                "lerobot.robots.so_follower": so_follower,
            },
        ):
            adapter = SO101LeRobotAdapter(
                port="/dev/tty.usbmodem-test",
                robot_id="minority_prophet_body",
                calibration_id=lerobot_calibration_digest(calibration),
                physical_execution_enabled=True,
            )
            adapter.connect()
            self.assertEqual(adapter.observe()["shoulder_pan.pos"], 0.0)
            target = {key: 1.0 for key in SO101_ACTION_KEYS}
            self.assertEqual(adapter.send_joint_target(target), target)
            adapter.disconnect()


if __name__ == "__main__":
    unittest.main()
