"""LeRobot SO-101 adapter for the standalone Embodiment Gateway."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Mapping

from embodiment_gateway import (
    EmbodimentAdapterManifest,
    EmbodimentContractError,
    EmbodimentExecutionError,
    canonical_digest,
    is_digest,
)


SO101_ACTION_KEYS = (
    "shoulder_pan.pos",
    "shoulder_lift.pos",
    "elbow_flex.pos",
    "wrist_flex.pos",
    "wrist_roll.pos",
    "gripper.pos",
)


def so101_values(value: Mapping[str, Any], *, label: str) -> dict[str, float]:
    if set(value) != set(SO101_ACTION_KEYS):
        raise EmbodimentContractError(f"{label} must contain the six SO-101 channels")
    result: dict[str, float] = {}
    for key in SO101_ACTION_KEYS:
        raw = value[key]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise EmbodimentContractError(f"{label}.{key} must be numeric")
        number = float(raw)
        lower, upper = (0.0, 100.0) if key == "gripper.pos" else (-100.0, 100.0)
        if not lower <= number <= upper:
            raise EmbodimentContractError(
                f"{label}.{key} must be within normalized range [{lower:g}, {upper:g}]"
            )
        result[key] = number
    return result


class MockSO101Adapter:
    """Deterministic SO-101-shaped adapter for CI without hardware."""

    def __init__(
        self,
        *,
        robot_id: str = "so101-mock",
        calibration_id: str = "mock-calibration-v1",
        initial_pose: Mapping[str, float] | None = None,
    ) -> None:
        self.manifest = EmbodimentAdapterManifest(
            adapter_id="embodiment-so101.mock",
            adapter_version="1.0.0",
            robot_family="so101",
            robot_id=robot_id,
            calibration_id=calibration_id,
            hardware=False,
            transport="in-process",
        )
        self._state = so101_values(
            initial_pose or {key: 0.0 for key in SO101_ACTION_KEYS},
            label="initial_pose",
        )
        self.connected = False
        self.commands: list[dict[str, float]] = []

    def connect(self) -> None:
        self.connected = True

    def observe(self) -> Mapping[str, float]:
        if not self.connected:
            raise EmbodimentExecutionError("mock SO-101 is not connected")
        return dict(self._state)

    def send_joint_target(self, target: Mapping[str, float]) -> Mapping[str, float]:
        if not self.connected:
            raise EmbodimentExecutionError("mock SO-101 is not connected")
        self._state = so101_values(target, label="target")
        self.commands.append(dict(self._state))
        return dict(self._state)

    def halt(self) -> None:
        return None

    def disconnect(self) -> None:
        self.connected = False


class SO101LeRobotAdapter:
    """Lazy LeRobot adapter for one calibrated SO-101 follower arm."""

    def __init__(
        self,
        *,
        port: str,
        robot_id: str,
        calibration_id: str,
        physical_execution_enabled: bool = False,
    ) -> None:
        if not port.strip() or not robot_id.strip() or not calibration_id.strip():
            raise EmbodimentContractError("port, robot_id, and calibration_id are required")
        if calibration_id != "discover" and not is_digest(calibration_id):
            raise EmbodimentContractError(
                "calibration_id must be its sha256 digest or 'discover' for inspection"
            )
        if calibration_id == "discover" and physical_execution_enabled:
            raise EmbodimentContractError("calibration discovery cannot enable execution")
        self.port = port
        self._physical_execution_enabled = physical_execution_enabled
        self.manifest = EmbodimentAdapterManifest(
            adapter_id="embodiment-so101.lerobot",
            adapter_version="1.0.0",
            robot_family="so101",
            robot_id=robot_id,
            calibration_id=calibration_id,
            hardware=True,
            transport=f"serial:{port}",
        )
        self._robot: Any | None = None

    def connect(self) -> None:
        if self._robot is not None:
            return
        try:
            from lerobot.robots.so_follower import (  # type: ignore[import-not-found]
                SO101Follower,
                SO101FollowerConfig,
            )
        except ImportError as error:
            raise EmbodimentExecutionError(
                "LeRobot support is unavailable; install embodiment-so101[hardware]"
            ) from error
        config = SO101FollowerConfig(
            port=self.port,
            id=self.manifest.robot_id,
            cameras={},
            use_degrees=False,
        )
        robot = SO101Follower(config)
        robot.connect(calibrate=False)
        if not robot.is_calibrated:
            robot.disconnect()
            raise EmbodimentExecutionError("SO-101 has no matching calibration")
        actual_calibration_id = lerobot_calibration_digest(robot.calibration)
        if self.manifest.calibration_id == "discover":
            self.manifest = EmbodimentAdapterManifest(
                adapter_id=self.manifest.adapter_id,
                adapter_version=self.manifest.adapter_version,
                robot_family=self.manifest.robot_family,
                robot_id=self.manifest.robot_id,
                calibration_id=actual_calibration_id,
                hardware=self.manifest.hardware,
                transport=self.manifest.transport,
            )
        elif actual_calibration_id != self.manifest.calibration_id:
            robot.disconnect()
            raise EmbodimentExecutionError("calibration digest does not match the sandbox")
        self._robot = robot

    def observe(self) -> Mapping[str, float]:
        if self._robot is None:
            raise EmbodimentExecutionError("SO-101 is not connected")
        observation = self._robot.get_observation()
        return so101_values(
            {key: float(observation[key]) for key in SO101_ACTION_KEYS},
            label="observation",
        )

    def send_joint_target(self, target: Mapping[str, float]) -> Mapping[str, float]:
        if self._robot is None:
            raise EmbodimentExecutionError("SO-101 is not connected")
        if not self._physical_execution_enabled:
            raise EmbodimentExecutionError("physical execution is disabled for this adapter")
        sent = self._robot.send_action(so101_values(target, label="target"))
        return so101_values(
            {key: float(sent[key]) for key in SO101_ACTION_KEYS},
            label="sent_action",
        )

    def halt(self) -> None:
        if self._robot is None:
            return
        try:
            self._robot.send_action(self.observe())
        except Exception:
            pass

    def disconnect(self) -> None:
        if self._robot is None:
            return
        robot, self._robot = self._robot, None
        robot.disconnect()


def lerobot_calibration_digest(calibration: Mapping[str, Any]) -> str:
    payload: dict[str, Any] = {}
    for name, item in sorted(calibration.items()):
        if is_dataclass(item):
            value = asdict(item)
        elif isinstance(item, Mapping):
            value = dict(item)
        elif hasattr(item, "__dict__"):
            value = {
                key: field
                for key, field in vars(item).items()
                if not key.startswith("_")
                and isinstance(field, (str, int, float, bool, type(None)))
            }
        else:
            raise EmbodimentExecutionError(
                f"cannot serialize LeRobot calibration entry {name!r}"
            )
        payload[str(name)] = value
    if not payload:
        raise EmbodimentExecutionError("LeRobot returned an empty calibration")
    return canonical_digest(payload)
