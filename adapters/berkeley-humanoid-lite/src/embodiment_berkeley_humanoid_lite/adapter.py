"""Digest-bound simulation adapter for Berkeley Humanoid Lite.

The gateway sees only its small adapter protocol. Upstream simulator imports,
tensor layout, model identity, and joint limits remain inside this plugin.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from embodiment_gateway import (
    EmbodimentAdapterManifest,
    EmbodimentContractError,
    EmbodimentExecutionError,
    canonical_digest,
    is_digest,
)


BERKELEY_HUMANOID_LITE_REPOSITORY = (
    "https://github.com/HybridRobotics/Berkeley-Humanoid-Lite"
)
PINNED_SOURCE_REVISION = "984741a3623c93b0583ccfdc479f1f8b1c4d900e"
PINNED_ASSETS_REVISION = "fc90fedd008b1e56a22e3c5221548d6b24f49707"
PINNED_LOWLEVEL_REVISION = "652777cc7c49884e7cd7ddfada758dc1979bf627"
PINNED_HUMANOID_CONFIG_DIGEST = (
    "sha256:b7eae066e7052751012b37ad6bab7541a5c47322b2821b180cec769664c8965f"
)
PINNED_HUMANOID_ACTION_CHANNELS = (
    "arm_left_shoulder_pitch_joint",
    "arm_left_shoulder_roll_joint",
    "arm_left_shoulder_yaw_joint",
    "arm_left_elbow_pitch_joint",
    "arm_left_elbow_roll_joint",
    "arm_right_shoulder_pitch_joint",
    "arm_right_shoulder_roll_joint",
    "arm_right_shoulder_yaw_joint",
    "arm_right_elbow_pitch_joint",
    "arm_right_elbow_roll_joint",
    "leg_left_hip_roll_joint",
    "leg_left_hip_yaw_joint",
    "leg_left_hip_pitch_joint",
    "leg_left_knee_pitch_joint",
    "leg_left_ankle_pitch_joint",
    "leg_left_ankle_roll_joint",
    "leg_right_hip_roll_joint",
    "leg_right_hip_yaw_joint",
    "leg_right_hip_pitch_joint",
    "leg_right_knee_pitch_joint",
    "leg_right_ankle_pitch_joint",
    "leg_right_ankle_roll_joint",
)


def _finite(number: Any, *, label: str) -> float:
    if isinstance(number, bool) or not isinstance(number, (int, float)):
        raise EmbodimentContractError(f"{label} must be numeric")
    value = float(number)
    if not math.isfinite(value):
        raise EmbodimentContractError(f"{label} must be finite")
    return value


@dataclass(frozen=True)
class HumanoidLiteSimulationProfile:
    """Exact simulation surface approved by an embodiment sandbox."""

    source_revision: str
    backend: str
    model_variant: str
    action_channels: tuple[str, ...]
    joint_limits: Mapping[str, tuple[float, float]]
    upstream_config_digest: str
    upstream_assets_revision: str
    upstream_lowlevel_revision: str
    source_repository: str = BERKELEY_HUMANOID_LITE_REPOSITORY

    def __post_init__(self) -> None:
        if not self.source_repository.startswith("https://"):
            raise EmbodimentContractError("source_repository must be an HTTPS URL")
        if not self.source_revision.strip():
            raise EmbodimentContractError("source_revision is required")
        if self.backend not in {"mujoco", "isaaclab", "mock"}:
            raise EmbodimentContractError("unsupported simulation backend")
        if self.backend != "mock" and (
            len(self.source_revision) != 40
            or any(character not in "0123456789abcdef" for character in self.source_revision)
        ):
            raise EmbodimentContractError(
                "non-mock source_revision must be an exact lowercase Git commit"
            )
        if not self.model_variant.strip():
            raise EmbodimentContractError("model_variant is required")
        if (
            not self.action_channels
            or len(self.action_channels) != len(set(self.action_channels))
            or any(not channel.strip() for channel in self.action_channels)
        ):
            raise EmbodimentContractError(
                "action_channels must be unique non-empty names"
            )
        if set(self.joint_limits) != set(self.action_channels):
            raise EmbodimentContractError(
                "joint_limits must exactly match action_channels"
            )
        normalized: dict[str, tuple[float, float]] = {}
        for channel in self.action_channels:
            raw = self.joint_limits[channel]
            if not isinstance(raw, (tuple, list)) or len(raw) != 2:
                raise EmbodimentContractError(
                    f"joint_limits.{channel} must contain [minimum, maximum]"
                )
            lower = _finite(raw[0], label=f"joint_limits.{channel}.minimum")
            upper = _finite(raw[1], label=f"joint_limits.{channel}.maximum")
            if lower >= upper:
                raise EmbodimentContractError(
                    f"joint_limits.{channel} minimum must be below maximum"
                )
            normalized[channel] = (lower, upper)
        object.__setattr__(self, "joint_limits", normalized)
        if not is_digest(self.upstream_config_digest):
            raise EmbodimentContractError(
                "upstream_config_digest must be sha256:<hex>"
            )
        for label, revision in (
            ("upstream_assets_revision", self.upstream_assets_revision),
            ("upstream_lowlevel_revision", self.upstream_lowlevel_revision),
        ):
            if self.backend == "mock":
                if not revision.strip():
                    raise EmbodimentContractError(f"{label} is required")
            elif (
                len(revision) != 40
                or any(character not in "0123456789abcdef" for character in revision)
            ):
                raise EmbodimentContractError(
                    f"{label} must be an exact lowercase Git commit"
                )

    def payload(self) -> dict[str, Any]:
        return {
            "source_repository": self.source_repository,
            "source_revision": self.source_revision,
            "backend": self.backend,
            "model_variant": self.model_variant,
            "action_channels": list(self.action_channels),
            "joint_limits": {
                channel: list(self.joint_limits[channel])
                for channel in self.action_channels
            },
            "upstream_config_digest": self.upstream_config_digest,
            "upstream_assets_revision": self.upstream_assets_revision,
            "upstream_lowlevel_revision": self.upstream_lowlevel_revision,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.payload())

    def values(self, value: Mapping[str, Any], *, label: str) -> dict[str, float]:
        if set(value) != set(self.action_channels):
            raise EmbodimentContractError(
                f"{label} channels must exactly match the bound simulation profile"
            )
        result: dict[str, float] = {}
        for channel in self.action_channels:
            number = _finite(value[channel], label=f"{label}.{channel}")
            lower, upper = self.joint_limits[channel]
            if not lower <= number <= upper:
                raise EmbodimentContractError(
                    f"{label}.{channel} must be within [{lower:g}, {upper:g}]"
                )
            result[channel] = number
        return result


def pinned_mujoco_humanoid_profile(
    *,
    joint_limits: Mapping[str, tuple[float, float]],
) -> HumanoidLiteSimulationProfile:
    """Profile for the exact upstream surface inspected by this adapter.

    Limits remain explicit because the upstream policy configuration's very
    broad action clip is not a suitable embodiment authorization boundary.
    """

    return HumanoidLiteSimulationProfile(
        source_revision=PINNED_SOURCE_REVISION,
        backend="mujoco",
        model_variant="humanoid-lite-v1.1-policy-humanoid",
        action_channels=PINNED_HUMANOID_ACTION_CHANNELS,
        joint_limits=joint_limits,
        upstream_config_digest=PINNED_HUMANOID_CONFIG_DIGEST,
        upstream_assets_revision=PINNED_ASSETS_REVISION,
        upstream_lowlevel_revision=PINNED_LOWLEVEL_REVISION,
    )


class JointTargetSimulationBackend(Protocol):
    """Simulator seam; no Gateway or product types cross it."""

    backend_id: str

    def connect(self) -> None: ...
    def observe(self) -> Mapping[str, float]: ...
    def step(self, target: Mapping[str, float]) -> Mapping[str, float]: ...
    def halt(self) -> None: ...
    def disconnect(self) -> None: ...


class MockHumanoidLiteBackend:
    """Deterministic, simulator-shaped backend for CI and contract tests."""

    backend_id = "mock"

    def __init__(self, initial_pose: Mapping[str, float]) -> None:
        self._state = dict(initial_pose)
        self.connected = False
        self.commands: list[dict[str, float]] = []

    def connect(self) -> None:
        self.connected = True

    def observe(self) -> Mapping[str, float]:
        if not self.connected:
            raise EmbodimentExecutionError("simulation is not connected")
        return dict(self._state)

    def step(self, target: Mapping[str, float]) -> Mapping[str, float]:
        if not self.connected:
            raise EmbodimentExecutionError("simulation is not connected")
        self._state = dict(target)
        self.commands.append(dict(target))
        return dict(self._state)

    def halt(self) -> None:
        return None

    def disconnect(self) -> None:
        self.connected = False


class OfficialMujocoBackend:
    """Lazy bridge to the upstream Berkeley Humanoid Lite MuJoCo simulator.

    The upstream observation layout is quaternion[4], angular velocity[3],
    action-joint positions[N], action-joint velocities[N], and command state[4].
    """

    backend_id = "mujoco"

    def __init__(self, *, cfg: Any, action_channels: Sequence[str]) -> None:
        if not action_channels:
            raise EmbodimentContractError("action_channels cannot be empty")
        self.cfg = cfg
        self.action_channels = tuple(action_channels)
        self._simulator: Any | None = None
        self._state: dict[str, float] | None = None

    def connect(self) -> None:
        if self._simulator is not None:
            return
        try:
            from berkeley_humanoid_lite.environments import MujocoSimulator
        except ImportError as error:
            raise EmbodimentExecutionError(
                "Berkeley Humanoid Lite MuJoCo support is unavailable; install "
                "the pinned upstream repository and its submodules"
            ) from error
        action_indices = tuple(int(index) for index in self.cfg.action_indices)
        if len(action_indices) != len(self.action_channels):
            raise EmbodimentExecutionError(
                "bound action channels do not match upstream cfg.action_indices"
            )
        simulator = MujocoSimulator(self.cfg)
        observation = simulator.reset()
        self._simulator = simulator
        self._state = self._positions(observation)

    def _positions(self, observation: Any) -> dict[str, float]:
        if hasattr(observation, "detach"):
            observation = observation.detach()
        if hasattr(observation, "cpu"):
            observation = observation.cpu()
        if hasattr(observation, "tolist"):
            observation = observation.tolist()
        values = list(observation)
        start = 7
        stop = start + len(self.action_channels)
        if len(values) < stop:
            raise EmbodimentExecutionError(
                "upstream observation is shorter than its documented joint layout"
            )
        return {
            channel: float(values[start + index])
            for index, channel in enumerate(self.action_channels)
        }

    def observe(self) -> Mapping[str, float]:
        if self._simulator is None or self._state is None:
            raise EmbodimentExecutionError("MuJoCo simulation is not connected")
        return dict(self._state)

    def step(self, target: Mapping[str, float]) -> Mapping[str, float]:
        if self._simulator is None:
            raise EmbodimentExecutionError("MuJoCo simulation is not connected")
        try:
            import torch
        except ImportError as error:
            raise EmbodimentExecutionError(
                "the upstream MuJoCo simulator requires torch"
            ) from error
        actions = torch.tensor(
            [target[channel] for channel in self.action_channels],
            dtype=torch.float32,
        )
        self._state = self._positions(self._simulator.step(actions))
        return dict(self._state)

    def halt(self) -> None:
        return None

    def disconnect(self) -> None:
        if self._simulator is None:
            return
        simulator, self._simulator = self._simulator, None
        self._state = None
        killed = getattr(simulator, "is_killed", None)
        if killed is not None and hasattr(killed, "set"):
            killed.set()
        viewer = getattr(simulator, "mj_viewer", None)
        if viewer is not None and hasattr(viewer, "close"):
            viewer.close()


class BerkeleyHumanoidLiteSimulationAdapter:
    """Simulation-only adapter implementing the neutral Gateway protocol."""

    def __init__(
        self,
        *,
        profile: HumanoidLiteSimulationProfile,
        backend: JointTargetSimulationBackend,
        robot_id: str = "berkeley-humanoid-lite-sim",
    ) -> None:
        if not robot_id.strip():
            raise EmbodimentContractError("robot_id is required")
        if getattr(backend, "backend_id", None) != profile.backend:
            raise EmbodimentContractError(
                "backend identity does not match the bound simulation profile"
            )
        self.profile = profile
        self.backend = backend
        self.manifest = EmbodimentAdapterManifest(
            adapter_id="embodiment-berkeley-humanoid-lite.simulation",
            adapter_version="1.0.0",
            robot_family="berkeley-humanoid-lite",
            robot_id=robot_id,
            calibration_id=profile.digest,
            hardware=False,
            transport=f"simulation:{profile.backend}",
        )
        self.connected = False

    def connect(self) -> None:
        self.backend.connect()
        try:
            self.profile.values(self.backend.observe(), label="initial_observation")
        except Exception:
            self.backend.disconnect()
            raise
        self.connected = True

    def observe(self) -> Mapping[str, float]:
        if not self.connected:
            raise EmbodimentExecutionError("simulation adapter is not connected")
        return self.profile.values(self.backend.observe(), label="observation")

    def send_joint_target(self, target: Mapping[str, float]) -> Mapping[str, float]:
        if not self.connected:
            raise EmbodimentExecutionError("simulation adapter is not connected")
        command = self.profile.values(target, label="target")
        return self.profile.values(self.backend.step(command), label="step_observation")

    def halt(self) -> None:
        self.backend.halt()

    def disconnect(self) -> None:
        try:
            self.backend.disconnect()
        finally:
            self.connected = False
