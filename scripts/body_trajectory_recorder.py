#!/usr/bin/env python3
"""Live, bounded writer for native body capture; contains no simulation/model.

Raw engine records keep their causal order. Dataset observation identifiers are
assigned separately, so native decision captures cannot collide with the normal
20 Hz observation sequence. Labels only refer to the exact native capture ID.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import queue
import threading
from typing import Any


class RecorderError(RuntimeError):
    pass


def approve_scripted_demonstration(episode_path: Path, teacher_registry: dict[str, Any]) -> dict[str, Any]:
    """Create a separate proven label view after new demonstrations have closed.

    The live/raw records are never rewritten. Existing stage 6 diagnostics have
    no collection-time teacher provenance and cannot be promoted by this API.
    """
    from body_trajectory_dataset import (approve_scripted_records, manifest_path,
                                          read_json, validate_episode)

    episode_path = episode_path.resolve()
    manifest_file = manifest_path(episode_path)
    manifest = read_json(manifest_file)
    if manifest.get("records_file", "records.jsonl") != "records.jsonl" or manifest.get("scripted_expert_approval") is not None:
        raise RecorderError("demonstration has already been approved")
    episode = validate_episode(episode_path)
    records, approval = approve_scripted_records(episode, teacher_registry)
    approved_path = episode_path / "records-approved.jsonl"
    original_manifest = episode_path / "trajectory-manifest-original.json"
    with approved_path.open("x", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)+"\n")
        stream.flush()
        os.fsync(stream.fileno())
    with original_manifest.open("xb") as stream:
        stream.write(manifest_file.read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    manifest.update(records_file=approved_path.name, scripted_expert_approval=approval)
    manifest["source_files"] += [{"path": approved_path.name, "sha256": sha256(approved_path)},
                                 {"path": original_manifest.name, "sha256": sha256(original_manifest)}]
    staged = manifest_file.with_suffix(".approved.tmp")
    staged.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    staged.replace(manifest_file)
    validate_episode(episode_path, teacher_registry=teacher_registry)
    return manifest


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


class BoundedJsonlWriter:
    """Disk IO on a separate Python thread; native simulation never writes it."""
    def __init__(self, paths: dict[str, Path], capacity: int = 256):
        self.queue: queue.Queue = queue.Queue(maxsize=capacity)
        self.paths = paths
        self.error: BaseException | None = None
        self.closed = False
        self.high_watermark = 0
        self.thread = threading.Thread(target=self._run, name="body-trajectory-writer", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        streams = {}
        try:
            streams = {name: path.open("x", encoding="utf-8") for name, path in self.paths.items()}
            while True:
                item = self.queue.get()
                if item is None:
                    break
                name, text = item
                streams[name].write(text+"\n")
            for stream in streams.values():
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException as error:
            self.error = error
        finally:
            for stream in streams.values():
                stream.close()

    def put(self, name: str, value: dict[str, Any]) -> None:
        if self.closed or self.error is not None or not self.thread.is_alive():
            raise RecorderError(f"trajectory writer unavailable: {self.error}")
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        try:
            self.queue.put((name, encoded), timeout=5)
        except queue.Full as error:
            raise RecorderError("bounded trajectory writer backpressure exceeded 5 seconds") from error
        self.high_watermark = max(self.high_watermark, self.queue.qsize())
        if self.error is not None:
            raise RecorderError(f"trajectory writer failed: {self.error}")

    def close(self) -> None:
        if self.closed:
            if self.error is not None:
                raise RecorderError(f"trajectory writer failed: {self.error}")
            return
        self.closed = True
        try:
            self.queue.put(None, timeout=5)
        except queue.Full as error:
            raise RecorderError("trajectory writer could not close its bounded queue") from error
        self.thread.join(timeout=10)
        if self.thread.is_alive() or self.error is not None:
            raise RecorderError(f"trajectory writer did not close durably: {self.error}")


class TrajectoryRecorder:
    def __init__(self, output: Path, *, episode_id: str, initial: dict[str, Any],
                 metadata: dict[str, Any], writer_capacity: int = 256):
        self.output, self.episode_id, self.initial, self.metadata = output, episode_id, initial, metadata
        self.paths = {"raw": output / "raw-trajectory.jsonl", "records": output / "records.jsonl",
                      "packets": output / "capture-packets.jsonl"}
        self.writer = BoundedJsonlWriter(self.paths, writer_capacity)
        self.counts: Counter = Counter()
        self.source_counts: Counter = Counter()
        self.record_seq = 0
        self.capture_generation: int | None = None
        self.first_raw_seq: int | None = None
        self.last_raw_seq: int | None = None
        self.max_queue_size = 0
        self.packet_count = 0
        self.observation_sequences: Counter = Counter()
        self.last_observation_time: dict[int, int] = {}
        self.observations: dict[int, dict[str, Any]] = {}
        self.latest: dict[int, dict[str, Any]] = {}
        self.periodic_refs: dict[tuple[int, ...], dict[str, Any]] = {}
        self.pending_capture: dict[int, dict[str, Any]] = {}
        self.decisions: dict[str, dict[str, Any]] = {}
        self.decision_refs: dict[tuple[Any, ...], str] = {}
        self.awaiting_acceptance: dict[tuple[Any, ...], tuple[str, dict[str, Any], dict[str, Any]]] = {}
        self.pending: dict[str, dict[str, Any]] = {}
        self.final_context: dict[str, Any] | None = None
        self.conditions: set[str] = set(metadata.get("conditions", []))
        self.target_visibility: dict[int, bool] = {}
        self.closed = False

    def _common(self, raw: dict[str, Any], entity: int | None = None) -> dict[str, Any]:
        actor = entity if entity is not None else raw.get("entity_id")
        if actor not in {row["entity_id"] for row in self.initial["actors"]}:
            raise RecorderError("capture refers to a non-episode actor")
        for key in ("session_epoch", "world_epoch"):
            if raw.get(key) != self.initial[key]:
                raise RecorderError("capture crosses an episode epoch")
        control = raw.get("control_generation", self.latest.get(actor, {}).get("control_generation", 0))
        return {"schema_version": 1, "episode_id": self.episode_id, "entity_id": actor,
                "session_epoch": raw["session_epoch"], "world_epoch": raw["world_epoch"],
                "control_generation": control, "t_game_ms": raw["t_game_ms"],
                "raw_record_seq": raw["record_seq"], "raw_capture_seq": raw["record_seq"],
                "capture_generation": raw["capture_generation"]}

    def _emit(self, record: dict[str, Any]) -> None:
        self.record_seq += 1
        record["record_seq"] = self.record_seq
        self.counts[record["kind"]] += 1
        self.writer.put("records", record)

    def ingest(self, packet: dict[str, Any], *, final: dict[str, Any] | None = None) -> None:
        if self.closed:
            raise RecorderError("cannot append to a closed episode")
        if final is not None:
            self.final_context = self._final_summary(final)
        if packet.get("capture_failed") is not False or packet.get("capture_failures_total") != 0:
            raise RecorderError(f"native capture failed: {packet.get('capture_failure_reason')}")
        for key in ("dropped_total", "dropped_since_drain", "reset_discarded_total"):
            if type(packet.get(key)) is not int or packet[key] != 0:
                raise RecorderError(f"native capture loss: {key}={packet.get(key)}")
        generation = packet.get("capture_generation")
        if type(generation) is not int or generation <= 0:
            raise RecorderError("native capture generation missing")
        if self.capture_generation is None:
            self.capture_generation = generation
        if generation != self.capture_generation:
            raise RecorderError("capture generation changed within an episode")
        rows = packet.get("events")
        if not isinstance(rows, list):
            raise RecorderError("capture packet has no event array")
        self.packet_count += 1
        self.writer.put("packets", {"packet_seq": self.packet_count, "stats": {key: value for key, value in packet.items() if key != "events"},
                                    "event_count": len(rows), "first_record_seq": rows[0].get("record_seq") if rows else None,
                                    "last_record_seq": rows[-1].get("record_seq") if rows else None})
        self.max_queue_size = max(self.max_queue_size, packet.get("queue_size", 0)+len(rows))
        for raw in rows:
            sequence = raw.get("record_seq")
            if type(sequence) is not int or sequence <= 0 or self.last_raw_seq is not None and sequence != self.last_raw_seq+1:
                raise RecorderError("native capture has a missing, repeated or reordered record")
            if raw.get("capture_generation") != generation:
                raise RecorderError("record belongs to another capture generation")
            self.first_raw_seq = sequence if self.first_raw_seq is None else self.first_raw_seq
            self.last_raw_seq = sequence
            self.source_counts[raw["kind"]] += 1
            self.writer.put("raw", raw)
            if raw["kind"] == "observation":
                self._observation(raw)
            elif raw["kind"] == "decision":
                self._decision(raw)
            elif raw["kind"] == "execution":
                self._execution(raw)
            elif raw["kind"] == "control":
                self._control(raw)
            else:
                raise RecorderError(f"unknown native capture kind: {raw['kind']}")

    def _observation(self, raw: dict[str, Any]) -> None:
        observation = raw.get("observation")
        if not isinstance(observation, dict):
            raise RecorderError("native capture observation missing")
        common = self._common(raw)
        entity = common["entity_id"]
        if observation.get("entity_id") != entity or observation.get("session_epoch") != common["session_epoch"] or observation.get("world_epoch") != common["world_epoch"]:
            raise RecorderError("observation identity differs from its capture wrapper")
        self.observation_sequences[entity] += 1
        previous_time = self.last_observation_time.get(entity, self.initial["game_ms"])
        sequence_dt = observation["t_game_ms"]-previous_time
        if sequence_dt < 0:
            raise RecorderError("actor observation time moves backwards in the causal stream")
        self.last_observation_time[entity] = observation["t_game_ms"]
        target = observation.get("target", {})
        visible = target.get("visible") is True
        if self.target_visibility.get(entity) is True and not visible:
            self.conditions.add("target_visibility_lost")
        self.target_visibility[entity] = visible
        if visible and target.get("in_w_range") is False:
            self.conditions.add("bad_distance")
        if visible and target.get("in_w_range") is True and target.get("focus_5") is True and target.get("melee_line_clear") is False:
            self.conditions.add("wall")
        common.update(kind="observation", source=raw["source"], observation_seq=self.observation_sequences[entity],
                      control_generation=observation["control_generation"], observation=observation,
                      raw_observation_seq=observation["observation_seq"], raw_observation_source=raw["source"],
                      sequence_dt_game_ms=sequence_dt, raw_dt_game_ms=observation["dt_game_ms"],
                      decision_valid=False, bc_label=None)
        self.observations[raw["record_seq"]] = common
        self.latest[entity] = common
        key = tuple(observation[field] for field in ("session_epoch", "world_epoch", "control_generation", "entity_id", "observation_seq"))
        if raw["source"] == "periodic":
            self.periodic_refs[key] = common
            self._emit(common)
            self._close_completed(common)
        elif raw["source"] == "native_capture":
            self.pending_capture[raw["record_seq"]] = common
        else:
            raise RecorderError("unknown observation capture source")

    def _native_key(self, raw: dict[str, Any]) -> tuple[Any, ...]:
        return (raw.get("source", "native"), raw["entity_id"], raw["decision_id"])

    def _decision(self, raw: dict[str, Any]) -> None:
        reference = raw.get("observation_record_seq")
        observation = self.pending_capture.pop(reference, None)
        if observation is None or observation["entity_id"] != raw.get("entity_id"):
            raise RecorderError("native decision lacks its exact pre-execution capture")
        action = raw.get("action_id")
        eligible = raw.get("decision_valid") is True
        mask = raw.get("action_mask")
        if eligible and (type(action) is not int or not 1 <= action < 12 or not isinstance(mask, list) or len(mask) != 12 or mask[action] is not True):
            raise RecorderError("native expert decision is outside its exact resolver mask")
        observation["label_mask"] = mask
        identity = f"native:{raw['entity_id']}:{raw['decision_id']}"
        common = self._common(raw)
        common.update(kind="decision", source="native", decision_id=identity, raw_decision_id=raw["decision_id"],
                      execution_id=raw["execution_id"], observation_seq=observation["observation_seq"],
                      raw_observation_record_seq=reference, action_id=action,
                      raw_intent=raw.get("native_intent"), effective_primitive=raw["effective_primitive"],
                      accepted=False, eligible=eligible, expert=False, decision_valid=False,
                      action_mask=mask, command_id=None, reason=raw.get("reason", ""))
        key = self._native_key(raw)
        if key in self.awaiting_acceptance or key in self.decision_refs:
            raise RecorderError("native decision recorded more than once")
        self.awaiting_acceptance[key] = (identity, common, observation)

    def _store_decision(self, identity: str, key: tuple[Any, ...], record: dict[str, Any], observation: dict[str, Any]) -> None:
        if identity in self.decisions or key in self.decision_refs:
            raise RecorderError("native decision recorded more than once")
        self._emit(record)
        self.decisions[identity] = record
        self.decision_refs[key] = identity
        self.pending[identity] = {"decision": record, "observation": observation, "started": False,
                                  "end_phase": None, "end_game_ms": None, "end_reason": None}

    def _execution(self, raw: dict[str, Any]) -> None:
        key = self._native_key(raw)
        awaiting = self.awaiting_acceptance.pop(key, None)
        if awaiting is not None:
            if raw.get("phase") not in ("accepted", "rejected"):
                raise RecorderError("native decision has no actual acceptance/rejection outcome")
            identity, decision, observation = awaiting
            accepted = raw["phase"] == "accepted"
            decision.update(accepted=accepted, expert=bool(accepted and decision["eligible"]),
                            decision_valid=bool(accepted and decision["eligible"]))
            observation["decision_valid"] = decision["expert"]
            observation["bc_label"] = decision["action_id"] if decision["expert"] else None
            self._emit(observation)
            self._close_completed(observation)
            self._store_decision(identity, key, decision, observation)
        identity = self.decision_refs.get(key)
        if identity is None:
            raise RecorderError("execution has no recorded native decision")
        self._execution_record(raw, identity, raw)

    def _execution_record(self, raw: dict[str, Any], identity: str, event: dict[str, Any]) -> None:
        decision = self.decisions[identity]
        if event.get("execution_id") != decision["execution_id"]:
            raise RecorderError("execution identity changed within one decision")
        common = self._common(raw)
        common.update(kind="execution", source=decision["source"], decision_id=identity,
                      execution_id=event["execution_id"], action_id=decision["action_id"],
                      phase=event["phase"], reason=event.get("reason", ""), effective_primitive=event["effective_primitive"])
        for key in ("physical_cancelled", "execution_incomplete", "pose_before", "pose_after", "segment_started"):
            if key in event:
                common[key] = event[key]
        self._emit(common)
        pending = self.pending.get(identity)
        if pending is None:
            raise RecorderError("execution follows a closed transition")
        if event["phase"] == "started":
            pending["started"] = True
        if event["phase"] in ("completed", "cancelled", "rejected"):
            if pending["end_phase"] is not None:
                raise RecorderError("execution has more than one terminal outcome")
            status = "episode_ended" if event.get("execution_incomplete") is True and event.get("physical_cancelled") is False else event["phase"]
            pending.update(end_phase=status, end_game_ms=event["t_game_ms"], end_reason=event.get("reason", ""))

    def _control(self, raw: dict[str, Any]) -> None:
        event = raw.get("body_control_event")
        if not isinstance(event, dict):
            raise RecorderError("scripted control wrapper lacks its raw event")
        command = event.get("command_id", 0)
        if not command or event.get("phase") not in ("accepted", "started", "continued", "consumed", "completed", "cancelled", "rejected"):
            return
        key = ("scripted", event["entity_id"], event["control_generation"], command)
        identity = self.decision_refs.get(key)
        if event["phase"] == "accepted":
            sample_key = tuple(event[field] for field in ("session_epoch", "world_epoch", "control_generation", "entity_id", "observation_seq"))
            observation = self.periodic_refs.get(sample_key)
            if observation is None:
                raise RecorderError("scripted decision lacks its exact provider observation")
            identity = f"scripted:{event['entity_id']}:{event['control_generation']}:{command}"
            action = event["action_id"]
            common = self._common(raw)
            common.update(kind="decision", source="scripted", decision_id=identity, raw_decision_id=command,
                          control_generation=event["control_generation"], execution_id=event["execution_id"],
                          observation_seq=observation["observation_seq"], raw_observation_record_seq=observation["raw_record_seq"],
                          action_id=action, raw_intent=None, effective_primitive=event["effective_primitive"],
                          accepted=True, eligible=observation["observation"].get("can_decide") is True,
                          expert=False, decision_valid=False, action_mask=observation["observation"]["action_mask"],
                          command_id=command, reason=event.get("reason", ""))
            self._store_decision(identity, key, common, observation)
        if identity is not None:
            normalized = {**raw, "control_generation": event["control_generation"]}
            self._execution_record(normalized, identity, event)

    def _close_completed(self, observation: dict[str, Any]) -> None:
        for identity, pending in list(self.pending.items()):
            decision = pending["decision"]
            if decision["entity_id"] != observation["entity_id"] or pending["end_game_ms"] is None:
                continue
            if observation["observation_seq"] <= decision["observation_seq"] or observation["t_game_ms"] < pending["end_game_ms"]:
                continue
            final = self.final_context if self.final_context is not None and observation["t_game_ms"] == self.final_context["t_game_ms"] else None
            if pending["end_phase"] == "episode_ended" and (final is None or observation["observation"].get("phase") != "post_simulation_post_animation"):
                continue
            self._transition(identity, observation, pending["end_phase"], final=final)

    def _transition(self, identity: str, observation: dict[str, Any] | None, status: str,
                    final: dict[str, Any] | None) -> None:
        pending = self.pending.pop(identity)
        decision = pending["decision"]
        next_time = observation["t_game_ms"] if observation is not None else final["t_game_ms"]
        duration = next_time-decision["t_game_ms"]
        if duration < 0:
            raise RecorderError("transition next observation predates its decision")
        common = {key: decision[key] for key in ("schema_version", "episode_id", "entity_id", "session_epoch",
                                                "world_epoch", "control_generation", "t_game_ms", "capture_generation")}
        infrastructure = final.get("infrastructure", False) if final else False
        common.update(kind="transition", decision_id=identity, execution_id=decision["execution_id"],
                      observation_seq=decision["observation_seq"], next_observation_seq=observation["observation_seq"] if observation else None,
                      raw_next_observation_record_seq=observation["raw_record_seq"] if observation else None,
                      duration_ms=duration, execution_status=status, uncompleted=status in ("episode_ended", "uncompleted"),
                      terminated=final.get("terminated", False) if final else False,
                      truncated=final.get("truncated", False) if final else False, infrastructure=infrastructure,
                      reason=(final.get("reason", "") if final else pending["end_reason"] or ""),
                      usable_for_bc=bool(decision["expert"] and pending["started"] and status == "completed" and not infrastructure and observation))
        self._emit(common)

    @staticmethod
    def _final_summary(final: dict[str, Any]) -> dict[str, Any]:
        return {"t_game_ms": final["game_ms"], "terminated": final["terminated"], "truncated": final["truncated"],
                "infrastructure": final["infrastructure"], "outcome": final["outcome"], "reason": final["reason"]}

    def finish(self, final: dict[str, Any], *, source_files: list[Path],
               runner_status: dict[str, Any], control_events: dict[str, Any]) -> dict[str, Any]:
        if self.pending_capture:
            raise RecorderError("native capture observation has no following decision")
        if self.awaiting_acceptance:
            raise RecorderError("native decision has no actual acceptance/rejection outcome")
        observer_stats = runner_status.get("body_observation")
        if not isinstance(observer_stats, dict):
            raise RecorderError("actual native observation counters are missing")
        for key in ("dropped_total", "event_dropped_total", "invalid_numeric_rows", "missed_period_buckets"):
            if type(observer_stats.get(key)) is not int or observer_stats[key] != 0:
                raise RecorderError(f"native observation recording lost data: {key}={observer_stats.get(key)}")
        if type(control_events.get("dropped_total")) is not int or control_events["dropped_total"] != 0:
            raise RecorderError("actual native control event queue lost data")
        summary = self._final_summary(final)
        for identity, pending in list(self.pending.items()):
            observation = self.latest.get(pending["decision"]["entity_id"])
            if observation is not None and (observation["observation_seq"] <= pending["decision"]["observation_seq"] or observation["t_game_ms"] != summary["t_game_ms"]):
                observation = None
            status = pending["end_phase"] or ("uncompleted" if summary["infrastructure"] else "episode_ended")
            self._transition(identity, observation, status, summary)
        self.writer.close()
        self.closed = True
        integrity_file = self.output / "source-integrity.json"
        integrity_file.write_text(json.dumps({"runner_status": runner_status, "control_events": control_events},
                                            ensure_ascii=False, indent=2, allow_nan=False)+"\n")
        files = list(self.paths.values())+[integrity_file]+source_files
        unique = {path.resolve(): path for path in files}
        sources = [{"path": str(path.relative_to(self.output)), "sha256": sha256(path)} for path in unique.values()]
        manifest = {"schema_version": 1, "trajectory_schema_version": 1, "episode_id": self.episode_id,
                    "feature_version": 1, "action_version": 1, **self.metadata,
                    "session_epoch": self.initial["session_epoch"], "world_epoch": self.initial["world_epoch"],
                    "actors": self.initial["actors"], "initial": self.initial, "final": summary,
                    "conditions": sorted(self.conditions),
                    "records_file": "records.jsonl", "raw_capture_file": "raw-trajectory.jsonl", "capture_file": "raw-trajectory.jsonl",
                    "capture_packets_file": "capture-packets.jsonl", "writer_closed": True,
                    "source_integrity_file": "source-integrity.json",
                    "counts": {kind: self.counts[kind] for kind in ("observation", "decision", "execution", "transition")}, "source_files": sources,
                    "source_integrity": {"gap_count": observer_stats["missed_period_buckets"],
                                         "dropped_observations": observer_stats["dropped_total"],
                                         "dropped_events": observer_stats["event_dropped_total"]+control_events["dropped_total"],
                                         "invalid_numeric_rows": observer_stats["invalid_numeric_rows"],
                                         "missed_period_buckets": observer_stats["missed_period_buckets"],
                                         "dropped_control_events": control_events["dropped_total"],
                                         "dropped_capture_records": 0, "reset_discarded_total": 0,
                                         "capture_failed": False, "capture_failures_total": 0,
                                         "capture_generation": self.capture_generation,
                                         "first_capture_record_seq": self.first_raw_seq, "last_capture_record_seq": self.last_raw_seq,
                                         "capture_records": sum(self.source_counts.values()), "capture_kind_counts": dict(self.source_counts),
                                         "capture_packets": self.packet_count,
                                         "max_native_queue_size": self.max_queue_size, "writer_high_watermark": self.writer.high_watermark}}
        (self.output / "trajectory-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False)+"\n")
        return manifest

    def abort(self) -> None:
        if not self.closed:
            self.writer.close()
            self.closed = True
