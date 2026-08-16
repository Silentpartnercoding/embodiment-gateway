"""Hardware-neutral, fail-closed execution of digest-bound judgments.

This package does not import an invention engine, an epistemic judge, or a
hardware SDK. It accepts a frozen root judgment, a human-approved sandbox, and
an adapter implementing the small protocol below.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol


ROOT_JUDGMENT_SCHEMA = "embodiment-gateway.root-judgment.v1"
EMBODIMENT_SANDBOX_SCHEMA = "embodiment-gateway.sandbox.v1"
EMBODIMENT_PLAN_SCHEMA = "embodiment-gateway.plan.v1"
EMBODIMENT_DECISION_SCHEMA = "embodiment-gateway.decision.v1"
EMBODIMENT_RECEIPT_SCHEMA = "embodiment-gateway.receipt.v1"
EMBODIMENT_LOG_SCHEMA = "embodiment-gateway.log-entry.v1"
EMBODIMENT_POLICY_ID = "root-judgment-bounded-embodiment.v1"


class EmbodimentContractError(ValueError):
    """An input is incomplete, unsafe, or digest-inconsistent."""


class EmbodimentExecutionError(RuntimeError):
    """An adapter failed or could not verify a commanded pose."""


class EmbodimentReplayError(EmbodimentExecutionError):
    """A prior physical intent has no completion and needs inspection."""


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def is_digest(value: object) -> bool:
    if not isinstance(value, str):
        return False
    prefix, separator, hexdigest = value.partition(":")
    return (
        prefix == "sha256"
        and separator == ":"
        and len(hexdigest) == 64
        and all(character in "0123456789abcdef" for character in hexdigest.lower())
    )


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise EmbodimentContractError(
            f"{label} fields mismatch; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _finite_values(
    value: Mapping[str, Any],
    *,
    label: str,
    expected_keys: set[str] | None = None,
) -> dict[str, float]:
    if expected_keys is not None:
        _require_exact_keys(value, expected_keys, label)
    if not value:
        raise EmbodimentContractError(f"{label} cannot be empty")
    result: dict[str, float] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not key.strip():
            raise EmbodimentContractError(f"{label} channel names must be non-empty strings")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise EmbodimentContractError(f"{label}.{key} must be a finite number")
        number = float(raw)
        if not math.isfinite(number):
            raise EmbodimentContractError(f"{label}.{key} must be finite")
        result[key] = number
    return result


@dataclass(frozen=True)
class RootJudgment:
    """Portable, hashable output from Minority Prophet or another root judge."""

    subject_id: str
    issuer: str
    verdict: str
    support_roots: tuple[str, ...]
    challenge_roots: tuple[str, ...]
    flip_budget: int
    conflicting_roots: tuple[str, ...]
    unattributed_claims: int
    issued_at: str
    schema: str = ROOT_JUDGMENT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != ROOT_JUDGMENT_SCHEMA:
            raise EmbodimentContractError("unsupported root judgment schema")
        if not self.subject_id.strip() or not self.issuer.strip() or not self.issued_at.strip():
            raise EmbodimentContractError("subject_id, issuer, and issued_at are required")
        if self.verdict not in {"true", "false", "abstain"}:
            raise EmbodimentContractError("verdict must be true, false, or abstain")
        if self.flip_budget < 0 or self.unattributed_claims < 0:
            raise EmbodimentContractError("root judgment counts cannot be negative")
        for label, roots in (
            ("support_roots", self.support_roots),
            ("challenge_roots", self.challenge_roots),
            ("conflicting_roots", self.conflicting_roots),
        ):
            if len(roots) != len(set(roots)) or any(not root.strip() for root in roots):
                raise EmbodimentContractError(f"{label} must contain unique non-empty roots")

    def payload(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "subject_id": self.subject_id,
            "issuer": self.issuer,
            "verdict": self.verdict,
            "support_roots": list(self.support_roots),
            "challenge_roots": list(self.challenge_roots),
            "flip_budget": self.flip_budget,
            "conflicting_roots": list(self.conflicting_roots),
            "unattributed_claims": self.unattributed_claims,
            "issued_at": self.issued_at,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.payload())

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "RootJudgment":
        expected = {
            "schema", "subject_id", "issuer", "verdict", "support_roots",
            "challenge_roots", "flip_budget", "conflicting_roots",
            "unattributed_claims", "issued_at",
        }
        _require_exact_keys(value, expected, "judgment")
        for key in ("support_roots", "challenge_roots", "conflicting_roots"):
            if not isinstance(value[key], list):
                raise EmbodimentContractError(f"judgment.{key} must be an array")
        return cls(
            subject_id=str(value["subject_id"]),
            issuer=str(value["issuer"]),
            verdict=str(value["verdict"]),
            support_roots=tuple(str(item) for item in value["support_roots"]),
            challenge_roots=tuple(str(item) for item in value["challenge_roots"]),
            flip_budget=int(value["flip_budget"]),
            conflicting_roots=tuple(str(item) for item in value["conflicting_roots"]),
            unattributed_claims=int(value["unattributed_claims"]),
            issued_at=str(value["issued_at"]),
            schema=str(value["schema"]),
        )

    @classmethod
    def from_root_verdict(
        cls,
        *,
        subject_id: str,
        issuer: str,
        verdict_payload: Mapping[str, Any],
        issued_at: str,
    ) -> "RootJudgment":
        """Wrap a provider-neutral root-verdict payload without importing its producer."""
        required = {
            "verdict", "support_roots", "challenge_roots", "flip_budget",
            "conflicting_roots", "unattributed_claims",
        }
        if not required.issubset(verdict_payload):
            raise EmbodimentContractError(
                f"root verdict is missing {sorted(required - set(verdict_payload))}"
            )
        for key in ("support_roots", "challenge_roots", "conflicting_roots"):
            if not isinstance(verdict_payload[key], list):
                raise EmbodimentContractError(f"root verdict {key} must be an array")
        return cls(
            subject_id=subject_id,
            issuer=issuer,
            verdict=str(verdict_payload["verdict"]),
            support_roots=tuple(str(item) for item in verdict_payload["support_roots"]),
            challenge_roots=tuple(str(item) for item in verdict_payload["challenge_roots"]),
            flip_budget=int(verdict_payload["flip_budget"]),
            conflicting_roots=tuple(str(item) for item in verdict_payload["conflicting_roots"]),
            unattributed_claims=int(verdict_payload["unattributed_claims"]),
            issued_at=issued_at,
        )


@dataclass(frozen=True)
class NamedPose:
    name: str
    joints: Mapping[str, float]

    def __post_init__(self) -> None:
        if not self.name or not self.name.replace("-", "_").isalnum():
            raise EmbodimentContractError("pose names must be non-empty identifiers")
        object.__setattr__(self, "joints", _finite_values(self.joints, label=self.name))

    def payload(self) -> dict[str, Any]:
        return {"name": self.name, "joints": dict(self.joints)}

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "NamedPose":
        _require_exact_keys(value, {"name", "joints"}, "pose")
        if not isinstance(value["joints"], Mapping):
            raise EmbodimentContractError("pose.joints must be an object")
        return cls(name=str(value["name"]), joints=value["joints"])


@dataclass(frozen=True)
class SandboxAuthorization:
    authorization_id: str
    robot_family: str
    robot_id: str
    calibration_id: str
    poses: tuple[NamedPose, ...]
    allowed_transitions: tuple[tuple[str, str], ...]
    max_pose_actions: int
    max_motor_commands: int
    max_normalized_delta_per_command: float
    pose_tolerance: float
    settle_timeout_seconds: float
    poll_interval_seconds: float
    approved_by: str
    approval_receipt_digest: str
    armed: bool
    workspace_cleared: bool
    external_estop_attested: bool
    physical_execution_authorized: bool
    schema: str = EMBODIMENT_SANDBOX_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != EMBODIMENT_SANDBOX_SCHEMA:
            raise EmbodimentContractError("unsupported sandbox schema")
        for label, value in (
            ("authorization_id", self.authorization_id),
            ("robot_family", self.robot_family),
            ("robot_id", self.robot_id),
            ("calibration_id", self.calibration_id),
            ("approved_by", self.approved_by),
        ):
            if not value.strip():
                raise EmbodimentContractError(f"{label} is required")
        if not is_digest(self.approval_receipt_digest):
            raise EmbodimentContractError("approval_receipt_digest must be sha256:<hex>")
        names = [pose.name for pose in self.poses]
        if not names or len(names) != len(set(names)):
            raise EmbodimentContractError("poses must be non-empty and uniquely named")
        channels = set(self.poses[0].joints)
        if any(set(pose.joints) != channels for pose in self.poses):
            raise EmbodimentContractError("all approved poses must use the same channels")
        known = {"START", *names}
        if not self.allowed_transitions:
            raise EmbodimentContractError("at least one allowed transition is required")
        normalized: list[tuple[str, str]] = []
        for transition in self.allowed_transitions:
            if len(transition) != 2 or transition[0] not in known or transition[1] not in known:
                raise EmbodimentContractError(f"invalid transition {transition!r}")
            if transition[1] == "START":
                raise EmbodimentContractError("START cannot be a transition target")
            normalized.append((str(transition[0]), str(transition[1])))
        object.__setattr__(self, "allowed_transitions", tuple(normalized))
        if not 1 <= self.max_pose_actions <= 100:
            raise EmbodimentContractError("max_pose_actions must be between 1 and 100")
        if not self.max_pose_actions <= self.max_motor_commands <= 10_000:
            raise EmbodimentContractError("invalid motor-command budget")
        if not 0.1 <= self.max_normalized_delta_per_command <= 25:
            raise EmbodimentContractError("invalid maximum command delta")
        if not 0.1 <= self.pose_tolerance <= 10:
            raise EmbodimentContractError("invalid pose tolerance")
        if not 0.1 <= self.settle_timeout_seconds <= 30:
            raise EmbodimentContractError("invalid settle timeout")
        if not 0.01 <= self.poll_interval_seconds <= 1:
            raise EmbodimentContractError("invalid polling interval")

    @property
    def pose_map(self) -> dict[str, NamedPose]:
        return {pose.name: pose for pose in self.poses}

    @property
    def channels(self) -> set[str]:
        return set(self.poses[0].joints)

    def payload(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "authorization_id": self.authorization_id,
            "robot_family": self.robot_family,
            "robot_id": self.robot_id,
            "calibration_id": self.calibration_id,
            "poses": [pose.payload() for pose in self.poses],
            "allowed_transitions": [list(item) for item in self.allowed_transitions],
            "max_pose_actions": self.max_pose_actions,
            "max_motor_commands": self.max_motor_commands,
            "max_normalized_delta_per_command": self.max_normalized_delta_per_command,
            "pose_tolerance": self.pose_tolerance,
            "settle_timeout_seconds": self.settle_timeout_seconds,
            "poll_interval_seconds": self.poll_interval_seconds,
            "approved_by": self.approved_by,
            "approval_receipt_digest": self.approval_receipt_digest,
            "armed": self.armed,
            "workspace_cleared": self.workspace_cleared,
            "external_estop_attested": self.external_estop_attested,
            "physical_execution_authorized": self.physical_execution_authorized,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.payload())

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "SandboxAuthorization":
        expected = {
            "schema", "authorization_id", "robot_family", "robot_id", "calibration_id",
            "poses", "allowed_transitions", "max_pose_actions", "max_motor_commands",
            "max_normalized_delta_per_command", "pose_tolerance", "settle_timeout_seconds",
            "poll_interval_seconds", "approved_by", "approval_receipt_digest", "armed",
            "workspace_cleared", "external_estop_attested", "physical_execution_authorized",
        }
        _require_exact_keys(value, expected, "sandbox")
        if not isinstance(value["poses"], list) or not isinstance(value["allowed_transitions"], list):
            raise EmbodimentContractError("poses and transitions must be arrays")
        return cls(
            authorization_id=str(value["authorization_id"]),
            robot_family=str(value["robot_family"]),
            robot_id=str(value["robot_id"]),
            calibration_id=str(value["calibration_id"]),
            poses=tuple(NamedPose.parse(item) for item in value["poses"]),
            allowed_transitions=tuple(tuple(str(part) for part in item) for item in value["allowed_transitions"]),
            max_pose_actions=int(value["max_pose_actions"]),
            max_motor_commands=int(value["max_motor_commands"]),
            max_normalized_delta_per_command=float(value["max_normalized_delta_per_command"]),
            pose_tolerance=float(value["pose_tolerance"]),
            settle_timeout_seconds=float(value["settle_timeout_seconds"]),
            poll_interval_seconds=float(value["poll_interval_seconds"]),
            approved_by=str(value["approved_by"]),
            approval_receipt_digest=str(value["approval_receipt_digest"]),
            armed=value["armed"] is True,
            workspace_cleared=value["workspace_cleared"] is True,
            external_estop_attested=value["external_estop_attested"] is True,
            physical_execution_authorized=value["physical_execution_authorized"] is True,
            schema=str(value["schema"]),
        )


@dataclass(frozen=True)
class EmbodimentPlan:
    run_id: str
    hypothesis_id: str
    judgment_digest: str
    sandbox_digest: str
    pose_sequence: tuple[str, ...]
    expected_final_pose: str
    purpose: str = "bounded embodiment"
    schema: str = EMBODIMENT_PLAN_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != EMBODIMENT_PLAN_SCHEMA:
            raise EmbodimentContractError("unsupported plan schema")
        if not self.run_id.strip() or not self.hypothesis_id.strip() or not self.purpose.strip():
            raise EmbodimentContractError("run_id, hypothesis_id, and purpose are required")
        if not is_digest(self.judgment_digest) or not is_digest(self.sandbox_digest):
            raise EmbodimentContractError("judgment and sandbox digests are required")
        if not self.pose_sequence:
            raise EmbodimentContractError("pose_sequence cannot be empty")
        if self.expected_final_pose != self.pose_sequence[-1]:
            raise EmbodimentContractError("expected_final_pose must be the final pose")

    def payload(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "run_id": self.run_id,
            "hypothesis_id": self.hypothesis_id,
            "judgment_digest": self.judgment_digest,
            "sandbox_digest": self.sandbox_digest,
            "pose_sequence": list(self.pose_sequence),
            "expected_final_pose": self.expected_final_pose,
            "purpose": self.purpose,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.payload())

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "EmbodimentPlan":
        expected = {
            "schema", "run_id", "hypothesis_id", "judgment_digest", "sandbox_digest",
            "pose_sequence", "expected_final_pose", "purpose",
        }
        _require_exact_keys(value, expected, "plan")
        if not isinstance(value["pose_sequence"], list):
            raise EmbodimentContractError("pose_sequence must be an array")
        return cls(
            run_id=str(value["run_id"]),
            hypothesis_id=str(value["hypothesis_id"]),
            judgment_digest=str(value["judgment_digest"]),
            sandbox_digest=str(value["sandbox_digest"]),
            pose_sequence=tuple(str(item) for item in value["pose_sequence"]),
            expected_final_pose=str(value["expected_final_pose"]),
            purpose=str(value["purpose"]),
            schema=str(value["schema"]),
        )


@dataclass(frozen=True)
class EmbodimentAdapterManifest:
    adapter_id: str
    adapter_version: str
    robot_family: str
    robot_id: str
    calibration_id: str
    hardware: bool
    transport: str

    def __post_init__(self) -> None:
        for label, value in (
            ("adapter_id", self.adapter_id),
            ("adapter_version", self.adapter_version),
            ("robot_family", self.robot_family),
            ("robot_id", self.robot_id),
            ("calibration_id", self.calibration_id),
            ("transport", self.transport),
        ):
            if not value.strip():
                raise EmbodimentContractError(f"adapter_manifest.{label} is required")
        if not isinstance(self.hardware, bool):
            raise EmbodimentContractError("adapter_manifest.hardware must be boolean")

    def payload(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "robot_family": self.robot_family,
            "robot_id": self.robot_id,
            "calibration_id": self.calibration_id,
            "hardware": self.hardware,
            "transport": self.transport,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.payload())

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "EmbodimentAdapterManifest":
        expected = {
            "adapter_id", "adapter_version", "robot_family", "robot_id",
            "calibration_id", "hardware", "transport",
        }
        _require_exact_keys(value, expected, "adapter_manifest")
        if not isinstance(value["hardware"], bool):
            raise EmbodimentContractError("adapter_manifest.hardware must be boolean")
        return cls(
            adapter_id=str(value["adapter_id"]),
            adapter_version=str(value["adapter_version"]),
            robot_family=str(value["robot_family"]),
            robot_id=str(value["robot_id"]),
            calibration_id=str(value["calibration_id"]),
            hardware=value["hardware"],
            transport=str(value["transport"]),
        )


class EmbodimentAdapter(Protocol):
    manifest: EmbodimentAdapterManifest

    def connect(self) -> None: ...
    def observe(self) -> Mapping[str, float]: ...
    def send_joint_target(self, target: Mapping[str, float]) -> Mapping[str, float]: ...
    def halt(self) -> None: ...
    def disconnect(self) -> None: ...


class MemoryAdapter:
    """Hardware-free adapter for testing the gateway contract itself."""

    def __init__(self, manifest: EmbodimentAdapterManifest, initial_pose: Mapping[str, float]):
        if manifest.hardware:
            raise EmbodimentContractError("MemoryAdapter cannot claim to be hardware")
        self.manifest = manifest
        self._state = _finite_values(initial_pose, label="initial_pose")
        self.connected = False
        self.commands: list[dict[str, float]] = []

    def connect(self) -> None:
        self.connected = True

    def observe(self) -> Mapping[str, float]:
        if not self.connected:
            raise EmbodimentExecutionError("memory adapter is not connected")
        return dict(self._state)

    def send_joint_target(self, target: Mapping[str, float]) -> Mapping[str, float]:
        if not self.connected:
            raise EmbodimentExecutionError("memory adapter is not connected")
        action = _finite_values(target, label="target", expected_keys=set(self._state))
        self._state = action
        self.commands.append(dict(action))
        return dict(action)

    def halt(self) -> None:
        return None

    def disconnect(self) -> None:
        self.connected = False


@dataclass(frozen=True)
class EmbodimentGatePolicy:
    policy_id: str = EMBODIMENT_POLICY_ID
    minimum_support_roots: int = 2
    minimum_flip_budget: int = 2

    def payload(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "minimum_support_roots": self.minimum_support_roots,
            "minimum_flip_budget": self.minimum_flip_budget,
            "scope": "preauthorized_named_pose_sandbox_only",
            "scientific_claim_authority": False,
            "evidence_root_authority": False,
        }


class EmbodimentReceiptLog:
    """Append-only intent/completion journal with a hash chain."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise EmbodimentReplayError(f"invalid journal JSON on line {line_number}") from error
            if not isinstance(row, dict):
                raise EmbodimentReplayError("journal entries must be objects")
            rows.append(row)
        return rows

    def state(self, run_id: str) -> dict[str, Any] | None:
        rows = [entry for entry in self.entries() if entry.get("run_id") == run_id]
        return rows[-1] if rows else None

    def begin(self, plan: EmbodimentPlan, sandbox: SandboxAuthorization) -> dict[str, Any]:
        existing = self.state(plan.run_id)
        if existing is not None:
            if existing.get("event") == "completion":
                return existing
            raise EmbodimentReplayError(
                "a prior physical intent lacks completion; inspect before a new run"
            )
        return self._append({
            "schema": EMBODIMENT_LOG_SCHEMA,
            "event": "intent",
            "run_id": plan.run_id,
            "plan_digest": plan.digest,
            "sandbox_digest": sandbox.digest,
            "created_at": _now(),
        })

    def complete(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        return self._append({
            "schema": EMBODIMENT_LOG_SCHEMA,
            "event": "completion",
            "run_id": receipt["run_id"],
            "receipt": dict(receipt),
            "created_at": _now(),
        })

    def verify(self) -> dict[str, Any]:
        previous: str | None = None
        rows = self.entries()
        for index, row in enumerate(rows):
            unsigned = {key: value for key, value in row.items() if key != "entry_digest"}
            if row.get("previous_entry_digest") != previous:
                return {"valid": False, "entries": len(rows), "error_index": index, "error": "chain"}
            if row.get("entry_digest") != canonical_digest(unsigned):
                return {"valid": False, "entries": len(rows), "error_index": index, "error": "digest"}
            previous = row["entry_digest"]
        return {"valid": True, "entries": len(rows), "head_digest": previous}

    def _append(self, body: Mapping[str, Any]) -> dict[str, Any]:
        rows = self.entries()
        previous = rows[-1]["entry_digest"] if rows else None
        unsigned = {**dict(body), "previous_entry_digest": previous}
        entry = {**unsigned, "entry_digest": canonical_digest(unsigned)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
        return entry


class RootJudgmentEmbodimentGateway:
    """Execute a qualifying external judgment inside one approved sandbox."""

    version = "1.0.0"

    def __init__(self, *, policy: EmbodimentGatePolicy | None = None) -> None:
        self.policy = policy or EmbodimentGatePolicy()

    def judge(
        self,
        judgment: RootJudgment,
        plan: EmbodimentPlan,
        sandbox: SandboxAuthorization,
        adapter: EmbodimentAdapter,
    ) -> dict[str, Any]:
        reasons = self._validate_boundary(judgment, plan, sandbox, adapter)
        if judgment.verdict == "false":
            disposition = "reject"
            reasons.append("negative_root_judgment")
        else:
            if judgment.verdict != "true":
                reasons.append("root_judgment_not_true")
            if len(judgment.support_roots) < self.policy.minimum_support_roots:
                reasons.append("insufficient_independent_support_roots")
            if judgment.flip_budget < self.policy.minimum_flip_budget:
                reasons.append("insufficient_flip_budget")
            if judgment.conflicting_roots:
                reasons.append("root_collision")
            if judgment.unattributed_claims:
                reasons.append("unattributed_pressure")
            disposition = "defer" if reasons else "pass"
        body = {
            "schema": EMBODIMENT_DECISION_SCHEMA,
            "gateway": {"name": type(self).__name__, "version": self.version},
            "policy": self.policy.payload(),
            "run_id": plan.run_id,
            "judgment_digest": judgment.digest,
            "plan_digest": plan.digest,
            "sandbox_digest": sandbox.digest,
            "adapter_manifest_digest": adapter.manifest.digest,
            "disposition": disposition,
            "reasons": sorted(set(reasons)),
            "mandatory_action_on_pass": True,
            "physical_execution_authorized": disposition == "pass" and adapter.manifest.hardware,
        }
        digest = canonical_digest(body)
        return {
            **body,
            "decision_id": "embodiment-decision:" + digest.removeprefix("sha256:"),
            "decision_digest": digest,
        }

    def run(
        self,
        judgment: RootJudgment,
        plan: EmbodimentPlan,
        sandbox: SandboxAuthorization,
        adapter: EmbodimentAdapter,
        *,
        journal: EmbodimentReceiptLog,
    ) -> dict[str, Any]:
        decision = self.judge(judgment, plan, sandbox, adapter)
        if decision["disposition"] != "pass":
            body = {
                "schema": EMBODIMENT_RECEIPT_SCHEMA,
                "run_id": plan.run_id,
                "status": "not_executed",
                "started_at": None,
                "completed_at": _now(),
                "judgment_digest": judgment.digest,
                "plan": plan.payload(),
                "plan_digest": plan.digest,
                "sandbox_digest": sandbox.digest,
                "adapter_manifest": adapter.manifest.payload(),
                "adapter_manifest_digest": adapter.manifest.digest,
                "decision": decision,
                "before": None,
                "steps": [],
                "motor_commands": 0,
                "final_pose_verified": False,
                "error": None,
                "mandatory_action_taken": False,
                "scientific_claim": False,
                "evidence_root_minted": False,
                "authority_scope": "embodiment_health_only",
            }
            return {**body, "receipt_digest": canonical_digest(body)}
        existing = journal.state(plan.run_id)
        if existing is not None and existing.get("event") == "completion":
            return dict(existing["receipt"])
        journal.begin(plan, sandbox)
        started_at = _now()
        steps: list[dict[str, Any]] = []
        status = "failed"
        error: str | None = None
        before: dict[str, float] | None = None
        connected = False
        motor_commands = 0
        try:
            adapter.connect()
            connected = True
            current = self._observation(adapter.observe(), sandbox)
            before = dict(current)
            for pose_name in plan.pose_sequence:
                target = dict(sandbox.pose_map[pose_name].joints)
                waypoints = _interpolate(current, target, sandbox.max_normalized_delta_per_command)
                if motor_commands + len(waypoints) > sandbox.max_motor_commands:
                    raise EmbodimentExecutionError("motion exceeds motor-command budget")
                sent: Mapping[str, float] = current
                for waypoint in waypoints:
                    sent = adapter.send_joint_target(waypoint)
                    motor_commands += 1
                observed = self._wait_for_pose(adapter, target, sandbox)
                verified = _within_tolerance(observed, target, sandbox.pose_tolerance)
                step = {
                    "pose": pose_name,
                    "target": target,
                    "sent": self._observation(sent, sandbox),
                    "observed": observed,
                    "verified": verified,
                    "motor_commands": len(waypoints),
                }
                step["step_digest"] = canonical_digest(step)
                steps.append(step)
                if not verified:
                    raise EmbodimentExecutionError(f"pose {pose_name!r} did not verify")
                current = observed
            status = "completed"
        except Exception as caught:
            error = f"{type(caught).__name__}: {str(caught)[:500]}"
            try:
                adapter.halt()
            except Exception:
                pass
        finally:
            if connected:
                try:
                    adapter.disconnect()
                except Exception as caught:
                    if error is None:
                        error = f"disconnect failed: {str(caught)[:500]}"
                        status = "failed"
        body = {
            "schema": EMBODIMENT_RECEIPT_SCHEMA,
            "run_id": plan.run_id,
            "status": status,
            "started_at": started_at,
            "completed_at": _now(),
            "judgment_digest": judgment.digest,
            "plan": plan.payload(),
            "plan_digest": plan.digest,
            "sandbox_digest": sandbox.digest,
            "adapter_manifest": adapter.manifest.payload(),
            "adapter_manifest_digest": adapter.manifest.digest,
            "decision": decision,
            "before": before,
            "steps": steps,
            "motor_commands": motor_commands,
            "final_pose_verified": status == "completed" and bool(steps) and steps[-1]["verified"],
            "error": error,
            "mandatory_action_taken": bool(steps),
            "scientific_claim": False,
            "evidence_root_minted": False,
            "authority_scope": "embodiment_health_only",
        }
        receipt = {**body, "receipt_digest": canonical_digest(body)}
        journal.complete(receipt)
        return receipt

    def _validate_boundary(
        self,
        judgment: RootJudgment,
        plan: EmbodimentPlan,
        sandbox: SandboxAuthorization,
        adapter: EmbodimentAdapter,
    ) -> list[str]:
        reasons: list[str] = []
        if plan.hypothesis_id != judgment.subject_id:
            reasons.append("judgment_subject_mismatch")
        if plan.judgment_digest != judgment.digest:
            reasons.append("judgment_digest_mismatch")
        if plan.sandbox_digest != sandbox.digest:
            reasons.append("sandbox_digest_mismatch")
        if adapter.manifest.robot_family != sandbox.robot_family:
            reasons.append("robot_family_mismatch")
        if adapter.manifest.robot_id != sandbox.robot_id:
            reasons.append("robot_id_mismatch")
        if adapter.manifest.calibration_id != sandbox.calibration_id:
            reasons.append("calibration_id_mismatch")
        if not sandbox.armed:
            reasons.append("sandbox_not_armed")
        if not sandbox.workspace_cleared:
            reasons.append("workspace_not_cleared")
        if adapter.manifest.hardware:
            if not sandbox.physical_execution_authorized:
                reasons.append("physical_execution_not_authorized")
            if not sandbox.external_estop_attested:
                reasons.append("external_estop_not_attested")
        if len(plan.pose_sequence) > sandbox.max_pose_actions:
            reasons.append("pose_action_budget_exceeded")
        known = sandbox.pose_map
        previous = "START"
        allowed = set(sandbox.allowed_transitions)
        for pose_name in plan.pose_sequence:
            if pose_name not in known:
                reasons.append("unknown_pose")
                continue
            if (previous, pose_name) not in allowed:
                reasons.append("transition_not_authorized")
            previous = pose_name
        return reasons

    @staticmethod
    def _observation(value: Mapping[str, float], sandbox: SandboxAuthorization) -> dict[str, float]:
        return _finite_values(value, label="observation", expected_keys=sandbox.channels)

    def _wait_for_pose(
        self,
        adapter: EmbodimentAdapter,
        target: Mapping[str, float],
        sandbox: SandboxAuthorization,
    ) -> dict[str, float]:
        deadline = time.monotonic() + sandbox.settle_timeout_seconds
        while True:
            observed = self._observation(adapter.observe(), sandbox)
            if _within_tolerance(observed, target, sandbox.pose_tolerance):
                return observed
            if time.monotonic() >= deadline:
                return observed
            time.sleep(sandbox.poll_interval_seconds)


def _interpolate(
    current: Mapping[str, float],
    target: Mapping[str, float],
    maximum_delta: float,
) -> list[dict[str, float]]:
    if set(current) != set(target):
        raise EmbodimentExecutionError("current and target channels differ")
    keys = tuple(sorted(target))
    largest = max(abs(target[key] - current[key]) for key in keys)
    segments = max(1, math.ceil(largest / maximum_delta))
    return [
        {key: current[key] + (target[key] - current[key]) * index / segments for key in keys}
        for index in range(1, segments + 1)
    ]


def _within_tolerance(
    observation: Mapping[str, float],
    target: Mapping[str, float],
    tolerance: float,
) -> bool:
    return set(observation) == set(target) and all(
        abs(observation[key] - target[key]) <= tolerance for key in target
    )


def verify_embodiment_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    """Verify one portable receipt without trusting its transport or producer.

    A valid digest proves internal integrity and binding. It is not a signature,
    device attestation, scientific claim, or evidence-admission decision.
    """
    expected = {
        "schema", "run_id", "status", "started_at", "completed_at",
        "judgment_digest", "plan", "plan_digest", "sandbox_digest",
        "adapter_manifest", "adapter_manifest_digest", "decision", "before",
        "steps", "motor_commands", "final_pose_verified", "error",
        "mandatory_action_taken", "scientific_claim", "evidence_root_minted",
        "authority_scope", "receipt_digest",
    }
    _require_exact_keys(value, expected, "receipt")
    if value["schema"] != EMBODIMENT_RECEIPT_SCHEMA:
        raise EmbodimentContractError("unsupported embodiment receipt schema")
    unsigned = {key: value[key] for key in value if key != "receipt_digest"}
    if value["receipt_digest"] != canonical_digest(unsigned):
        raise EmbodimentContractError("receipt_digest does not bind the receipt payload")
    if value["status"] not in {"completed", "failed", "not_executed"}:
        raise EmbodimentContractError("unsupported embodiment receipt status")
    if not isinstance(value["run_id"], str) or not value["run_id"].strip():
        raise EmbodimentContractError("receipt.run_id is required")
    if not is_digest(value["judgment_digest"]):
        raise EmbodimentContractError("receipt.judgment_digest must be sha256:<hex>")
    if not isinstance(value["plan"], Mapping):
        raise EmbodimentContractError("receipt.plan must be an object")
    plan = EmbodimentPlan.parse(value["plan"])
    if value["plan_digest"] != plan.digest:
        raise EmbodimentContractError("receipt.plan_digest does not bind plan")
    if value["run_id"] != plan.run_id:
        raise EmbodimentContractError("receipt.run_id does not match plan")
    if value["judgment_digest"] != plan.judgment_digest:
        raise EmbodimentContractError("receipt judgment does not match plan")
    if value["sandbox_digest"] != plan.sandbox_digest:
        raise EmbodimentContractError("receipt sandbox does not match plan")
    if not isinstance(value["adapter_manifest"], Mapping):
        raise EmbodimentContractError("receipt.adapter_manifest must be an object")
    manifest = EmbodimentAdapterManifest.parse(value["adapter_manifest"])
    if value["adapter_manifest_digest"] != manifest.digest:
        raise EmbodimentContractError("adapter_manifest_digest does not bind manifest")
    _verify_decision(
        value["decision"],
        run_id=plan.run_id,
        judgment_digest=plan.judgment_digest,
        plan_digest=plan.digest,
        sandbox_digest=plan.sandbox_digest,
        adapter_manifest_digest=manifest.digest,
    )
    if not isinstance(value["steps"], list):
        raise EmbodimentContractError("receipt.steps must be an array")
    if isinstance(value["motor_commands"], bool) or not isinstance(value["motor_commands"], int):
        raise EmbodimentContractError("receipt.motor_commands must be an integer")
    if value["motor_commands"] < 0:
        raise EmbodimentContractError("receipt.motor_commands cannot be negative")
    for key in (
        "final_pose_verified", "mandatory_action_taken", "scientific_claim",
        "evidence_root_minted",
    ):
        if not isinstance(value[key], bool):
            raise EmbodimentContractError(f"receipt.{key} must be boolean")
    if value["scientific_claim"] or value["evidence_root_minted"]:
        raise EmbodimentContractError("embodiment receipts cannot assert science or mint roots")
    if value["authority_scope"] != "embodiment_health_only":
        raise EmbodimentContractError("unsupported embodiment authority scope")
    if value["status"] == "not_executed":
        if value["decision"]["disposition"] == "pass":
            raise EmbodimentContractError("a passing decision cannot be marked not_executed")
        if value["steps"] or value["motor_commands"] or value["mandatory_action_taken"]:
            raise EmbodimentContractError("not_executed receipt contains execution activity")
    elif value["decision"]["disposition"] != "pass":
        raise EmbodimentContractError("an executed receipt requires a passing decision")
    if value["status"] == "completed" and not value["final_pose_verified"]:
        raise EmbodimentContractError("completed receipt did not verify its final pose")
    return {
        "valid": True,
        "schema": value["schema"],
        "run_id": plan.run_id,
        "hypothesis_id": plan.hypothesis_id,
        "status": value["status"],
        "hardware": manifest.hardware,
        "receipt_digest": value["receipt_digest"],
        "authority_scope": value["authority_scope"],
    }


def _verify_decision(
    value: Any,
    *,
    run_id: str,
    judgment_digest: str,
    plan_digest: str,
    sandbox_digest: str,
    adapter_manifest_digest: str,
) -> None:
    if not isinstance(value, Mapping):
        raise EmbodimentContractError("receipt.decision must be an object")
    expected = {
        "schema", "gateway", "policy", "run_id", "judgment_digest", "plan_digest",
        "sandbox_digest", "adapter_manifest_digest", "disposition", "reasons",
        "mandatory_action_on_pass", "physical_execution_authorized", "decision_id",
        "decision_digest",
    }
    _require_exact_keys(value, expected, "decision")
    if value["schema"] != EMBODIMENT_DECISION_SCHEMA:
        raise EmbodimentContractError("unsupported embodiment decision schema")
    unsigned = {
        key: value[key]
        for key in value
        if key not in {"decision_id", "decision_digest"}
    }
    digest = canonical_digest(unsigned)
    if value["decision_digest"] != digest:
        raise EmbodimentContractError("decision_digest does not bind decision")
    if value["decision_id"] != "embodiment-decision:" + digest.removeprefix("sha256:"):
        raise EmbodimentContractError("decision_id does not match decision digest")
    bindings = {
        "run_id": run_id,
        "judgment_digest": judgment_digest,
        "plan_digest": plan_digest,
        "sandbox_digest": sandbox_digest,
        "adapter_manifest_digest": adapter_manifest_digest,
    }
    for key, expected_value in bindings.items():
        if value[key] != expected_value:
            raise EmbodimentContractError(f"decision {key} does not match receipt")
    if value["disposition"] not in {"pass", "defer", "reject"}:
        raise EmbodimentContractError("unsupported embodiment decision disposition")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
