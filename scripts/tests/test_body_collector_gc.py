"""Pure fake-collector checks; no models, runners, or real GC mutations."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from body_collector_gc import (BOOTSTRAP_EXECUTABLE, BOOTSTRAP_EXECUTABLE_SHA256,
                               BOOTSTRAP_RESOLVED_EXECUTABLE,
                               CollectorGCScope, CollectorStaticGCProfile)


class FakeGC:
    def __init__(self, *, enabled=True, frozen=0):
        self.enabled = enabled
        self.thresholds = (700, 10, 10)
        self.frozen = frozen
        self.callbacks = []
        self.calls = []
        self.fail_collect = False
        self.fail_freeze = False
        self.fail_unfreeze = False
        self.mutate_on_freeze = False

    def isenabled(self):
        self.calls.append("isenabled")
        return self.enabled

    def get_threshold(self):
        self.calls.append("get_threshold")
        return self.thresholds

    def get_freeze_count(self):
        self.calls.append("get_freeze_count")
        return self.frozen

    def collect(self):
        self.calls.append("collect")
        if self.fail_collect:
            self.thresholds = (1, 2, 3)
            self.enabled = False
            raise ValueError("collection failed")
        return 3

    def freeze(self):
        self.calls.append("freeze")
        self.frozen = 37
        if self.mutate_on_freeze:
            self.enabled = False
            self.thresholds = (1, 2, 3)
        if self.fail_freeze:
            raise ValueError("freeze failed after mutation")

    def unfreeze(self):
        self.calls.append("unfreeze")
        if self.fail_unfreeze:
            self.fail_unfreeze = False
            raise ValueError("unfreeze failed")
        self.frozen = 0

    def enable(self):
        self.calls.append("enable")
        self.enabled = True

    def set_threshold(self, *thresholds):
        self.calls.append("set_threshold")
        self.thresholds = thresholds

    def emit(self, phase, **info):
        for callback in list(self.callbacks):
            callback(phase, info)


class Clock:
    def __init__(self):
        self.readings = []

    def __call__(self):
        value = 100+len(self.readings)*10
        self.readings.append(value)
        return value


def test_opt_out_never_reads_or_mutates_collector():
    collector = FakeGC(enabled=False, frozen=11)
    clock = Clock()
    profile = CollectorStaticGCProfile(gc_module=collector, clock=clock)
    assert profile.activate() is None
    assert profile.enter() is profile.profile
    assert profile.close() is profile.profile
    assert profile.activation_proof is None and profile.restoration_proof is None
    assert collector.calls == [] and clock.readings == []
    assert not collector.enabled and collector.frozen == 11


@pytest.mark.parametrize("enabled", [1, "true", None])
def test_opt_in_requires_an_explicit_boolean(enabled):
    collector = FakeGC()
    with pytest.raises(TypeError, match="booleans"):
        CollectorGCScope(enabled, collector=collector)
    assert collector.calls == []


@pytest.mark.parametrize("enabled,frozen", [(False, 0), (True, 5)])
def test_preexisting_disabled_or_frozen_collector_is_not_changed(enabled, frozen):
    collector = FakeGC(enabled=enabled, frozen=frozen)
    scope = CollectorGCScope(True, collector=collector)
    with pytest.raises(RuntimeError, match="preexisting"):
        scope.enter()
    scope.close()
    assert collector.calls == ["isenabled", "get_threshold", "get_freeze_count"]
    assert collector.enabled is enabled and collector.frozen == frozen
    assert scope.activation_proof is None and scope.restoration_proof is None


def test_collect_then_freeze_keeps_dynamic_gc_and_records_actual_proof():
    collector, clock = FakeGC(), Clock()
    scope = CollectorGCScope(True, collector=collector, clock=clock)
    result = scope.enter()
    assert result is scope.profile
    assert result["kind"] == "body_collector_static_gc_profile"
    assert result["mode"] == "freeze_static_keep_dynamic"
    assert result["schema_version"] == 1 and result["enabled"] is True
    assert result["max_events"] == 65536
    assert len(result["helper_sha256"]) == 64
    assert Path(result["helper_path"]).name == "body_collector_gc.py"
    assert collector.calls == ["isenabled", "get_threshold", "get_freeze_count",
                               "collect", "freeze", "isenabled", "get_threshold", "get_freeze_count"]
    assert scope.activation_proof == {
        "begin_wall_monotonic_ns": 100, "end_wall_monotonic_ns": 110,
        "gc_enabled_before": True, "gc_enabled_after": True,
        "thresholds_before": [700, 10, 10], "thresholds_after": [700, 10, 10],
        "freeze_count_before": 0, "freeze_count_after": 37,
        "collected_before_freeze": 3,
    }
    collector.emit("start", generation=0)
    collector.emit("stop", generation=0, collected=2, uncollectable=1)
    assert result["dynamic_gc_events"] == [{
        "generation": 0, "begin_wall_monotonic_ns": 120,
        "end_wall_monotonic_ns": 130, "collected": 2, "uncollectable": 1,
    }]
    scope.close()
    assert scope.restoration_proof == {
        "begin_wall_monotonic_ns": 140, "end_wall_monotonic_ns": 150,
        "owned_freeze_released": True,
        "gc_enabled_before": True, "gc_enabled_after": True,
        "thresholds_before": [700, 10, 10], "thresholds_after": [700, 10, 10],
        "freeze_count_before": 37, "freeze_count_after": 0,
    }
    assert collector.enabled and collector.thresholds == (700, 10, 10)
    assert collector.frozen == 0 and collector.callbacks == []


def test_optional_collection_and_repeated_enter_close_are_idempotent():
    collector = FakeGC()
    scope = CollectorGCScope(True, collector=collector, collect_before_freeze=False)
    scope.activate()
    calls = list(collector.calls)
    assert scope.enter() is scope.profile and collector.calls == calls
    assert "collect" not in calls and scope.activation_proof["collected_before_freeze"] is None
    scope.close()
    calls = list(collector.calls)
    assert scope.close() is scope.profile and collector.calls == calls
    with pytest.raises(RuntimeError, match="already been closed"):
        scope.enter()
    assert collector.calls == calls


def test_caller_exception_finally_restores_original_gc_policy():
    collector = FakeGC()
    scope = CollectorGCScope(True, collector=collector)
    with pytest.raises(ValueError, match="runner failed"):
        try:
            scope.enter()
            collector.enabled = False
            collector.thresholds = (1, 2, 3)
            raise ValueError("runner failed")
        finally:
            scope.close()
    assert collector.frozen == 0 and collector.enabled
    assert collector.thresholds == (700, 10, 10)
    assert collector.calls.count("enable") == collector.calls.count("set_threshold") == 1
    assert scope.restoration_proof["owned_freeze_released"] is True


def test_freeze_error_after_partial_mutation_is_cleaned_up():
    collector = FakeGC()
    collector.fail_freeze = collector.mutate_on_freeze = True
    scope = CollectorGCScope(True, collector=collector)
    with pytest.raises(ValueError, match="freeze failed"):
        scope.enter()
    assert collector.frozen == 0 and collector.enabled
    assert collector.thresholds == (700, 10, 10) and collector.callbacks == []
    assert collector.calls.count("unfreeze") == 1
    calls = list(collector.calls)
    scope.close()
    assert collector.calls == calls


def test_collection_error_restores_policy_without_claiming_a_freeze():
    collector = FakeGC()
    collector.fail_collect = True
    scope = CollectorGCScope(True, collector=collector)
    with pytest.raises(ValueError, match="collection failed"):
        scope.enter()
    assert collector.enabled and collector.thresholds == (700, 10, 10)
    assert collector.frozen == 0 and "freeze" not in collector.calls
    assert "unfreeze" not in collector.calls


def test_failed_activation_policy_check_releases_owned_freeze():
    collector = FakeGC()
    collector.mutate_on_freeze = True
    scope = CollectorGCScope(True, collector=collector)
    with pytest.raises(RuntimeError, match="changed dynamic GC"):
        scope.enter()
    assert collector.enabled and collector.frozen == 0
    assert collector.thresholds == (700, 10, 10) and not scope.owned


def test_close_attempts_policy_restoration_even_when_unfreeze_fails_and_can_retry():
    collector = FakeGC()
    scope = CollectorGCScope(True, collector=collector)
    scope.enter()
    collector.enabled = False
    collector.thresholds = (1, 2, 3)
    collector.fail_unfreeze = True
    with pytest.raises(RuntimeError, match="not restored"):
        scope.close()
    assert collector.enabled and collector.thresholds == (700, 10, 10)
    assert collector.frozen == 37 and scope.owned
    scope.close()
    assert collector.frozen == 0 and not scope.owned


def test_incomplete_or_overflowed_dynamic_events_are_reported():
    collector = FakeGC()
    scope = CollectorGCScope(True, collector=collector)
    scope.MAX_EVENTS = 1
    scope.enter()
    collector.emit("stop", generation=1)
    for _ in range(2):
        collector.emit("start", generation=0)
        collector.emit("stop", generation=0)
    collector.emit("start", generation=2)
    scope.close()
    assert len(scope.events) == 1 and scope.profile["dropped_total"] == 3
    assert scope.pending == {} and collector.callbacks == []


def bootstrap_interpreter(**overrides):
    return {"python_version": [3, 12, 13], "python_implementation": "cpython",
            "sys_version": "3.12.13 private bootstrap fixture",
            "executable": BOOTSTRAP_EXECUTABLE,
            "resolved_executable": BOOTSTRAP_RESOLVED_EXECUTABLE,
            "executable_sha256": BOOTSTRAP_EXECUTABLE_SHA256, **overrides}


def test_known_bootstrap_requires_opt_in_before_normalization_and_static_freeze():
    collector, clock = FakeGC(frozen=375), Clock()
    scope = CollectorGCScope(True, collector=collector, clock=clock,
                             normalize_bootstrap=True,
                             interpreter_metadata=bootstrap_interpreter())
    scope.enter()
    proof = scope.profile["bootstrap_normalization"]
    assert scope.profile["normalize_bootstrap"] is True
    assert proof == {
        "kind": "body_collector_bootstrap_gc_normalization",
        **bootstrap_interpreter(), "interpreter_metadata_source": "injected_metadata",
        "begin_wall_monotonic_ns": 110, "end_wall_monotonic_ns": 120,
        "gc_enabled_before": True, "gc_enabled_after": True,
        "thresholds_before": [700, 10, 10], "thresholds_after": [700, 10, 10],
        "freeze_count_before": 375, "freeze_count_after": 0,
    }
    assert scope.activation_proof["freeze_count_before"] == 0
    assert scope.activation_proof["begin_wall_monotonic_ns"] == 130
    assert scope.activation_proof["begin_wall_monotonic_ns"] > proof["end_wall_monotonic_ns"]
    assert collector.calls.index("unfreeze") < collector.calls.index("collect") < collector.calls.index("freeze")
    scope.close()
    assert collector.calls.count("unfreeze") == 2
    assert collector.enabled and collector.thresholds == (700, 10, 10) and collector.frozen == 0


def test_known_bootstrap_is_untouched_without_explicit_normalization():
    collector = FakeGC(frozen=375)
    scope = CollectorGCScope(True, collector=collector,
                             interpreter_metadata=bootstrap_interpreter())
    with pytest.raises(RuntimeError, match="preexisting"):
        scope.enter()
    scope.close()
    assert collector.frozen == 375 and "unfreeze" not in collector.calls
    assert "bootstrap_normalization" not in scope.profile


@pytest.mark.parametrize("frozen,overrides", [
    (374, {}), (376, {}),
    (375, {"python_version": [3, 12, 12]}),
    (375, {"python_version": [3, 14, 4]}),
    (375, {"python_implementation": "pypy"}),
    (375, {"executable": "/foreign/python"}),
    (375, {"resolved_executable": "/foreign/uv/python3.12"}),
    (375, {"executable_sha256": "0"*64}),
])
def test_unknown_freeze_or_interpreter_is_refused_without_gc_mutation(frozen, overrides):
    collector = FakeGC(frozen=frozen)
    scope = CollectorGCScope(True, collector=collector, normalize_bootstrap=True,
                             interpreter_metadata=bootstrap_interpreter(**overrides))
    with pytest.raises(RuntimeError, match="unknown"):
        scope.enter()
    scope.close()
    assert collector.calls == ["isenabled", "get_threshold", "get_freeze_count"]
    assert collector.frozen == frozen and collector.enabled
    assert "bootstrap_normalization" not in scope.profile


def test_disabled_gc_is_refused_even_for_known_bootstrap_with_opt_in():
    collector = FakeGC(enabled=False, frozen=375)
    scope = CollectorGCScope(True, collector=collector, normalize_bootstrap=True,
                             interpreter_metadata=bootstrap_interpreter())
    with pytest.raises(RuntimeError, match="enabled dynamic GC"):
        scope.enter()
    assert "unfreeze" not in collector.calls and collector.frozen == 375


def test_bootstrap_normalization_policy_mutation_is_restored_before_failure():
    class MutatingUnfreeze(FakeGC):
        def unfreeze(self):
            super().unfreeze()
            self.enabled = False
            self.thresholds = (1, 2, 3)

    collector = MutatingUnfreeze(frozen=375)
    scope = CollectorGCScope(True, collector=collector, normalize_bootstrap=True,
                             interpreter_metadata=bootstrap_interpreter())
    with pytest.raises(RuntimeError, match="normalization changed"):
        scope.enter()
    assert collector.frozen == 0 and collector.enabled
    assert collector.thresholds == (700, 10, 10)
    assert "freeze" not in collector.calls and scope.activation_proof is None
    calls = list(collector.calls)
    scope.close()
    assert collector.calls == calls


def test_bootstrap_unfreeze_partial_error_cleans_up_owned_bootstrap():
    class PartialUnfreeze(FakeGC):
        def unfreeze(self):
            super().unfreeze()
            if not hasattr(self, "raised"):
                self.raised = True
                self.enabled = False
                self.thresholds = (1, 2, 3)
                raise ValueError("bootstrap unfreeze failed after mutation")

    collector = PartialUnfreeze(frozen=375)
    scope = CollectorGCScope(True, collector=collector, normalize_bootstrap=True,
                             interpreter_metadata=bootstrap_interpreter())
    with pytest.raises(ValueError, match="bootstrap unfreeze failed"):
        scope.enter()
    assert collector.frozen == 0 and collector.enabled
    assert collector.thresholds == (700, 10, 10)
    assert collector.calls.count("unfreeze") == 2 and "freeze" not in collector.calls


def test_bootstrap_opt_in_is_boolean_and_opt_out_still_does_not_touch_gc():
    with pytest.raises(TypeError, match="booleans"):
        CollectorGCScope(True, normalize_bootstrap="true")
    collector = FakeGC(frozen=375)
    scope = CollectorGCScope(False, collector=collector, normalize_bootstrap=True,
                             interpreter_metadata=bootstrap_interpreter())
    scope.enter()
    scope.close()
    assert collector.calls == [] and collector.frozen == 375
