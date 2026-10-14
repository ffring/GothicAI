#!/usr/bin/env python3
"""Check native birth velocity against independent ordinary alive motion samples."""

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import struct


LABEL_MODES = {"stationary": "stationary", "falling_dead": "dead", "falling_ko": "ko"}
VELOCITY_LIMIT = .001  # cm/s, unchanged from the native save/velocity contracts
MAX_GAP_MS = 250
MAX_SPEED_CM_S = 3000
PART_COUNT = 24


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("nonfinite JSON number: " + value)


def finite_json_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("overflowed JSON number: " + value)
    return result


def number(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("invalid finite number: " + name)
    return value


def counter(value, name):
    if type(value) is not int or not 0 <= value <= 0xffffffffffffffff:
        raise ValueError("invalid unsigned counter: " + name)
    return value


def vector(value, name):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError("invalid three-component vector: " + name)
    return [number(component, name) for component in value]


def binary32(value):
    # %.9g is round-trippable for engine floats. Decimal formatting at a large world position
    # must not become artificial motion in the independent derivative.
    result = struct.unpack("<f", struct.pack("<f", value))[0]
    if not math.isfinite(result):
        raise ValueError("observed value is outside the native binary32 domain")
    return result


def sample(value, name):
    if not isinstance(value, dict):
        raise ValueError("invalid alive sample: " + name)
    tick = counter(value["t_game_ms"], name + " clock")
    if type(value["in_air"]) is not bool:
        raise ValueError("invalid alive in_air flag: " + name)
    position = tuple(binary32(v) for v in vector(value["position_cm"], name + " position"))
    return tick, value["in_air"], position


def distance(left, right):
    result = math.hypot(*(a-b for a, b in zip(left, right)))
    if not math.isfinite(result):
        raise ValueError("nonfinite derived velocity delta")
    return result


def derivative(previous, current):
    elapsed = current[0] - previous[0]
    if not 0 < elapsed <= MAX_GAP_MS:
        raise ValueError("alive derivative interval is not distinct, monotonic and fresh")
    # Only observed alive XYZ positions and clock deltas feed this oracle. Reproduce the native
    # scalar precision explicitly, without reading the NPC cache or claimed expected velocities.
    ratio = binary32(1000.0/binary32(elapsed))
    return [binary32(binary32(end-start)*ratio)
            for start, end in zip(previous[2], current[2])]


def capped_velocity(velocity):
    squares = [binary32(value*value) for value in velocity]
    length = binary32(math.sqrt(binary32(binary32(squares[0]+squares[1])+squares[2])))
    if length <= MAX_SPEED_CM_S:
        return velocity, False
    ratio = binary32(MAX_SPEED_CM_S/length)
    return [binary32(value*ratio) for value in velocity], True


def parse_lines(text):
    events = []
    for index, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            raise ValueError("blank JSON event at line " + str(index))
        event = json.loads(line, object_pairs_hook=unique_object, parse_constant=reject_constant,
                           parse_float=finite_json_float)
        if not isinstance(event, dict) or not isinstance(event.get("kind"), str):
            raise ValueError("invalid event object at line " + str(index))
        if event["kind"] == "ragdoll_error":
            raise ValueError("actual scenario reported a ragdoll_error")
        events.append(event)
    if not events:
        raise ValueError("ordinary motion event stream is empty")
    return events


def check(events):
    streams = {label: {"live": [], "birth": [], "continuation": []} for label in LABEL_MODES}
    kinds = {"ragdoll_birth_live": "live", "ragdoll_birth": "birth",
             "ragdoll_birth_continuation": "continuation"}
    for event in events:
        kind = event["kind"]
        if kind == "ragdoll_error":
            raise ValueError("actual scenario reported a ragdoll_error")
        if kind not in kinds:
            if kind.startswith("ragdoll_birth"):
                raise ValueError("unknown birth probe event kind: " + kind)
            continue
        label = event["label"]
        if type(label) is not str or label not in streams:
            raise ValueError("unknown birth probe label")
        counter(event["actor_id"], "actor_id")
        counter(event["t_game_ms"], "t_game_ms")
        streams[label][kinds[kind]].append(event)

    proof = {}
    for label, stream in streams.items():
        if len(stream["birth"]) != 1:
            raise ValueError(label + ": requires one unique native birth")
        birth = stream["birth"][0]
        actor, born = birth["actor_id"], birth["t_game_ms"]
        if type(birth["mode"]) is not str or birth["mode"] != LABEL_MODES[label]:
            raise ValueError(label + ": wrong down-event mode")
        if type(birth["parts"]) is not int or birth["parts"] != PART_COUNT:
            raise ValueError(label + ": native birth is not the complete 24-part human body")
        if len(stream["live"]) < 4:
            raise ValueError(label + ": lacks four independently observed alive samples")
        live = []
        for event in stream["live"]:
            if event["actor_id"] != actor:
                raise ValueError(label + ": alive actor identity changed")
            observed = sample(event, label + " live")
            if observed[0] > born or (live and observed[0] <= live[-1][0]):
                raise ValueError(label + ": alive observations are duplicated, unordered or after birth")
            live.append(observed)
        raw_history = birth["history"]
        if not isinstance(raw_history, list) or len(raw_history) < 4:
            raise ValueError(label + ": native birth lacks four ordinary-motion history samples")
        history = [sample(value, label + " history") for value in raw_history]
        if history[-1][0] != born:
            raise ValueError(label + ": history does not end at the actual birth tick")
        for previous, current in zip(history, history[1:]):
            derivative(previous, current)
        # A self-consistent expected velocity/history cannot pass without the independent live
        # observations which establish the real movement before death or unconsciousness.
        if len(history) > len(live) or history != live[-len(history):]:
            raise ValueError(label + ": birth history is not the observed consecutive live suffix")

        uncapped_velocity = derivative(history[-2], history[-1])
        observed_velocity, capped = capped_velocity(uncapped_velocity)
        expected = vector(birth["expected_velocity_cm_s"], label + " claimed birth velocity")
        expected_delta = distance(expected, observed_velocity)
        if expected_delta > VELOCITY_LIMIT:
            raise ValueError(label + ": claimed velocity differs from independent XYZ/time derivative")
        velocities = birth["velocities_cm_s"]
        if not isinstance(velocities, list) or len(velocities) != PART_COUNT:
            raise ValueError(label + ": missing or incorrect-count native part velocities")
        maximum_delta = 0.0
        for index, value in enumerate(velocities):
            actual = vector(value, label + " native part " + str(index))
            delta = distance(actual, observed_velocity)
            maximum_delta = max(maximum_delta, delta)
            if distance(actual, expected) > VELOCITY_LIMIT or delta > VELOCITY_LIMIT:
                raise ValueError(label + ": a native part lost the independently measured birth velocity")

        falling_intervals = 0
        for previous, current in reversed(list(zip(history, history[1:]))):
            if previous[1] and current[1] and derivative(previous, current)[1] < 0:
                falling_intervals += 1
            else:
                break
        if label == "stationary":
            if distance(expected, [0, 0, 0]) > VELOCITY_LIMIT:
                raise ValueError("stationary: expected velocity is not zero within the fixed gate")
        elif falling_intervals < 3 or expected[1] >= -50 or observed_velocity[1] >= -50:
            raise ValueError(label + ": birth lacks three consecutive ordinary falling intervals")

        born_y = number(birth["birth_pelvis_y_cm"], label + " birth pelvis")
        continuation = stream["continuation"]
        if not continuation:
            raise ValueError(label + ": missing first positive native continuation")
        times = []
        for event in continuation:
            if event["actor_id"] != actor or type(event["native_parts"]) is not int or event["native_parts"] != PART_COUNT:
                raise ValueError(label + ": continuation lost actor identity or the full native body")
            tick = event["t_game_ms"]
            if tick <= born or (times and tick <= times[-1]):
                raise ValueError(label + ": continuation ticks must be positive, distinct and ordered")
            number(event["pelvis_y_cm"], label + " continued pelvis")
            times.append(tick)
        if times[0]-born > MAX_GAP_MS:
            raise ValueError(label + ": continuation does not cover the next fresh native tick")
        first_y = continuation[0]["pelvis_y_cm"]
        if label != "stationary" and first_y >= born_y:
            raise ValueError(label + ": pelvis did not fall on the first positive native tick")
        proof[label] = {
            "actor_id": actor, "birth_game_ms": born, "parts": PART_COUNT,
            "observed_live_samples": len(live), "matched_history_samples": len(history),
            "last_interval_ms": history[-1][0]-history[-2][0],
            "independent_uncapped_velocity_cm_s": uncapped_velocity,
            "producer_speed_limit_applied": capped,
            "independent_velocity_cm_s": observed_velocity, "claimed_velocity_cm_s": expected,
            "claimed_velocity_delta_cm_s": expected_delta,
            "max_native_velocity_delta_cm_s": maximum_delta,
            "consecutive_falling_intervals": falling_intervals,
            "first_native_dt_ms": times[0]-born, "birth_pelvis_y_cm": born_y,
            "first_native_pelvis_y_cm": first_y, "first_native_pelvis_drop_cm": born_y-first_y,
        }
    return proof


def self_test():
    events = []
    for actor, label in enumerate(LABEL_MODES, 1):
        falling = label != "stationary"
        history = [{"t_game_ms": tick, "in_air": falling,
                    "position_cm": [0, 100-index if falling else 100, 0]}
                   for index, tick in enumerate((1000, 1010, 1020, 1030))]
        events += [{"kind": "ragdoll_birth_live", "label": label, "actor_id": actor, **value}
                   for value in history]
        velocity = [0, -100 if falling else 0, 0]
        events.append({"kind": "ragdoll_birth", "label": label, "actor_id": actor,
                       "t_game_ms": 1030, "mode": LABEL_MODES[label], "history": copy.deepcopy(history),
                       "expected_velocity_cm_s": velocity, "parts": PART_COUNT,
                       "velocities_cm_s": [list(velocity) for _ in range(PART_COUNT)], "birth_pelvis_y_cm": 50})
        events.append({"kind": "ragdoll_birth_continuation", "label": label, "actor_id": actor,
                       "t_game_ms": 1040, "pelvis_y_cm": 49 if falling else 50, "native_parts": PART_COUNT})
    check(events)
    controls = 0

    def rejected(mutator):
        nonlocal controls
        damaged = copy.deepcopy(events)
        mutator(damaged)
        try:
            check(damaged)
        except (ValueError, TypeError, KeyError, OverflowError):
            controls += 1
            return
        raise ValueError("self-test accepted a damaged birth oracle fixture")

    def birth(data, label="falling_dead"):
        return next(value for value in data if value["kind"] == "ragdoll_birth" and value["label"] == label)

    def continued(data):
        return next(value for value in data if value["kind"] == "ragdoll_birth_continuation" and value["label"] == "falling_dead")

    rejected(lambda data: data.append({"kind": "ragdoll_error"}))
    rejected(lambda data: data.append(copy.deepcopy(birth(data))))
    rejected(lambda data: birth(data).__setitem__("parts", 12))
    rejected(lambda data: birth(data).__setitem__("parts", True))
    rejected(lambda data: birth(data)["velocities_cm_s"].pop())
    rejected(lambda data: birth(data)["velocities_cm_s"][0].__setitem__(1, 0))
    rejected(lambda data: birth(data).__setitem__("expected_velocity_cm_s", [0, 0, 0]))
    rejected(lambda data: birth(data)["history"][-1].__setitem__("t_game_ms", 1029))
    rejected(lambda data: birth(data)["history"][1].__setitem__("t_game_ms", 1000))
    rejected(lambda data: birth(data)["history"][0].__setitem__("in_air", False))
    rejected(lambda data: birth(data)["history"].pop(0))
    rejected(lambda data: birth(data)["history"][0]["position_cm"].__setitem__(0, 20))
    rejected(lambda data: birth(data)["history"][0].__setitem__("in_air", 1))
    rejected(lambda data: birth(data)["history"][0].__setitem__("position_cm", [0, 1]))
    rejected(lambda data: birth(data)["velocities_cm_s"][0].__setitem__(0, float("nan")))
    rejected(lambda data: birth(data)["velocities_cm_s"][0].__setitem__(0, True))
    rejected(lambda data: birth(data).__setitem__("mode", "ko"))
    rejected(lambda data: birth(data, "stationary").__setitem__("expected_velocity_cm_s", [1, 0, 0]))
    rejected(lambda data: continued(data).__setitem__("pelvis_y_cm", 50))
    rejected(lambda data: continued(data).__setitem__("t_game_ms", 1030))
    rejected(lambda data: continued(data).__setitem__("t_game_ms", 1300))
    rejected(lambda data: continued(data).__setitem__("native_parts", 12))
    rejected(lambda data: continued(data).__setitem__("actor_id", 99))
    for malformed in ('{"kind":"x","kind":"y"}', '{"kind":"x","value":NaN}',
                      '{"kind":"x","unused":1e400}',
                      '{"kind":"x","nested":{"a":1,"a":2}}', '[]', ''):
        try:
            parse_lines(malformed)
        except ValueError:
            controls += 1
        else:
            raise ValueError("self-test accepted malformed JSON")
    return {"positive_three_label_fixture": True, "rejected_controls": controls}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, nargs="?")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--self-test", action="store_true", help="check asset-free comparator controls instead of a game run")
    args = parser.parse_args()
    result = {"schema_version": 1, "pass": False,
              "scope": "ordinary alive XYZ/time derivative at native birth; stationary/dead/KO and first positive native continuation",
              "limits": {"velocity_cm_s": VELOCITY_LIMIT, "fresh_interval_ms": MAX_GAP_MS,
                         "human_native_parts": PART_COUNT, "ordinary_speed_cm_s": MAX_SPEED_CM_S,
                         "falling_y_cm_s_less_than": -50}}
    try:
        if args.self_test:
            if args.run is not None:
                raise ValueError("self-test does not take an actual game directory")
            result["self_test"] = self_test()
            result["scope"] = "asset-free comparator controls; does not prove an actual game run"
        else:
            if args.run is None:
                raise ValueError("actual game directory is required")
            source = args.run / "events.jsonl"
            raw = source.read_bytes()
            result["events_sha256"] = hashlib.sha256(raw).hexdigest()
            events = parse_lines(raw.decode("utf-8"))
            result["proof"] = check(events)
            result["events_file"] = str(source)
        result["pass"] = True
    except (ValueError, TypeError, KeyError, OSError, OverflowError, struct.error) as error:
        result["error"] = str(error)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print("PASS" if result["pass"] else "FAIL")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
