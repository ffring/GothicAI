"""Process-lifetime static GC isolation after model warm-up, before runners.

Only the warmed static heap is frozen. Dynamic collection stays enabled with
the caller's thresholds, and ``close`` releases only this profile's freeze.
"""
from __future__ import annotations

import gc
import hashlib
from pathlib import Path
import sys
import time


BOOTSTRAP_PYTHON_VERSION = (3, 12, 13)
BOOTSTRAP_FREEZE_COUNT = 375
BOOTSTRAP_EXECUTABLE = "/home/kaifat/gothic-ai/body/.venv/bin/python"
BOOTSTRAP_RESOLVED_EXECUTABLE = "/home/kaifat/.local/share/uv/python/cpython-3.12.13-linux-x86_64-gnu/bin/python3.12"
BOOTSTRAP_EXECUTABLE_SHA256 = "021044895e95be79dc2f110367607e684119afbc8ce75f6f0eec94844e0acec7"


class CollectorGCScope:
    MAX_EVENTS = 65536

    def __init__(self, enabled=False, *, collector=gc, clock=time.monotonic_ns,
                 collect_before_freeze=True, gc_module=None,
                 normalize_bootstrap=False, interpreter_metadata=None):
        if any(type(value) is not bool for value in (enabled, collect_before_freeze, normalize_bootstrap)):
            raise TypeError("collector GC options must be explicit booleans")
        self.enabled = enabled
        self.gc = collector if gc_module is None else gc_module
        self.clock = clock
        self.collect_before_freeze = collect_before_freeze
        self.normalize_bootstrap = normalize_bootstrap
        self._interpreter_metadata = interpreter_metadata
        self.owned = False
        self.events, self.pending, self.dropped = [], {}, 0
        self._original = None
        self._freeze_attempted = False
        self._restore_needed = False
        self._closed = False
        path = Path(__file__).resolve()
        self.profile = {
            "schema_version": 1, "kind": "body_collector_static_gc_profile",
            "mode": "freeze_static_keep_dynamic", "enabled": enabled,
            "normalize_bootstrap": normalize_bootstrap,
            "helper_path": str(path),
            "helper_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "dynamic_gc_events": self.events, "dropped_total": 0,
            "max_events": self.MAX_EVENTS,
        }

    @property
    def activation_proof(self):
        return self.profile.get("startup")

    @property
    def restoration_proof(self):
        return self.profile.get("restore")

    def _state(self):
        return {"gc_enabled": self.gc.isenabled(),
                "thresholds": list(self.gc.get_threshold()),
                "freeze_count": self.gc.get_freeze_count()}

    def _interpreter(self):
        if self._interpreter_metadata is not None:
            return {**self._interpreter_metadata,
                    "interpreter_metadata_source": "injected_metadata"}
        executable = Path(sys.executable)
        resolved = executable.resolve()
        return {"python_version": list(sys.version_info[:3]),
                "python_implementation": sys.implementation.name,
                "sys_version": sys.version,
                "executable": str(executable),
                "resolved_executable": str(resolved),
                "executable_sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
                "interpreter_metadata_source": "runtime_sys_and_executable_bytes"}

    def _normalize_bootstrap(self, before):
        # This private interpreter was measured with -I -S before all imports:
        # its bootstrap owns exactly 375 frozen objects. No other preexisting
        # freeze, interpreter binary, or process executable is accepted.
        if not self.normalize_bootstrap or before["freeze_count"] != BOOTSTRAP_FREEZE_COUNT:
            raise RuntimeError("static GC scope refuses unknown preexisting freeze ownership")
        interpreter = self._interpreter()
        expected = {"python_version": list(BOOTSTRAP_PYTHON_VERSION),
                    "python_implementation": "cpython",
                    "executable": BOOTSTRAP_EXECUTABLE,
                    "resolved_executable": BOOTSTRAP_RESOLVED_EXECUTABLE,
                    "executable_sha256": BOOTSTRAP_EXECUTABLE_SHA256}
        if any(interpreter.get(key) != value for key, value in expected.items()):
            raise RuntimeError("static GC scope refuses unknown bootstrap interpreter identity")
        begin = self.clock()
        self._original = {**before, "freeze_count": 0}
        self._restore_needed = True
        self._freeze_attempted = True
        try:
            self.gc.unfreeze()
            self._freeze_attempted = False
            after = self._state()
            self.profile["bootstrap_normalization"] = {
                "kind": "body_collector_bootstrap_gc_normalization",
                **interpreter,
                "begin_wall_monotonic_ns": begin,
                "end_wall_monotonic_ns": self.clock(),
                **{key+"_before": value for key, value in before.items()},
                **{key+"_after": value for key, value in after.items()},
            }
            if (after["gc_enabled"] is not True
                    or after["thresholds"] != before["thresholds"]
                    or after["freeze_count"] != 0):
                raise RuntimeError("bootstrap normalization changed dynamic GC policy or retained frozen objects")
            return after
        except BaseException:
            self.close()
            raise

    def _callback(self, phase, info):
        generation = info["generation"]
        if phase == "start":
            if generation in self.pending:
                self.dropped += 1
            self.pending[generation] = self.clock()
        elif phase == "stop":
            begin = self.pending.pop(generation, None)
            end = self.clock()
            if begin is None or len(self.events) >= self.MAX_EVENTS:
                self.dropped += 1
            else:
                self.events.append({"generation": generation,
                                    "begin_wall_monotonic_ns": begin,
                                    "end_wall_monotonic_ns": end,
                                    "collected": info.get("collected", 0),
                                    "uncollectable": info.get("uncollectable", 0)})
        self.profile["dropped_total"] = self.dropped

    def activate(self):
        if not self.enabled:
            return None
        return self.enter()

    def enter(self):
        if not self.enabled or self.owned:
            return self.profile
        if self._closed:
            raise RuntimeError("collector GC profile has already been closed")
        begin = self.clock()
        before = self._state()
        if before["gc_enabled"] is not True:
            raise RuntimeError("static GC scope requires enabled dynamic GC and no preexisting freeze ownership")
        if before["freeze_count"] != 0:
            before = self._normalize_bootstrap(before)
            begin = self.clock()
        self._original = before
        self._restore_needed = True
        try:
            collected = self.gc.collect() if self.collect_before_freeze else None
            # A collector may freeze objects and then raise. Mark ownership
            # before calling it so the exception path still unfreezes them.
            self._freeze_attempted = True
            self.gc.freeze()
            self.owned = True
            after = self._state()
            self.profile["startup"] = {
                "begin_wall_monotonic_ns": begin,
                "end_wall_monotonic_ns": self.clock(),
                "collected_before_freeze": collected,
                **{key+"_before": value for key, value in before.items()},
                **{key+"_after": value for key, value in after.items()},
            }
            if after["gc_enabled"] is not True or before["thresholds"] != after["thresholds"] or after["freeze_count"] <= 0:
                raise RuntimeError("static freeze changed dynamic GC policy or froze no static objects")
            self.gc.callbacks.append(self._callback)
            return self.profile
        except BaseException:
            self.close()
            raise

    def close(self):
        if not self._restore_needed:
            return self.profile
        begin = self.clock()
        errors = []
        before = None
        try:
            before = self._state()
        except BaseException as error:
            errors.append(error)
        # Attempt every restoration even when one operation raises. Retain
        # ownership on failure, allowing a later close to retry safely.
        try:
            if self._callback in self.gc.callbacks:
                self.gc.callbacks.remove(self._callback)
            if self.pending:
                self.dropped += len(self.pending)
                self.pending.clear()
                self.profile["dropped_total"] = self.dropped
        except BaseException as error:
            errors.append(error)
        if self._freeze_attempted:
            try:
                self.gc.unfreeze()
                self._freeze_attempted = False
                self.owned = False
            except BaseException as error:
                errors.append(error)
        try:
            if self.gc.isenabled() is not True:
                self.gc.enable()
        except BaseException as error:
            errors.append(error)
        try:
            if list(self.gc.get_threshold()) != self._original["thresholds"]:
                self.gc.set_threshold(*self._original["thresholds"])
        except BaseException as error:
            errors.append(error)
        after = self._state()
        self.profile["restore"] = {
            "begin_wall_monotonic_ns": begin,
            "end_wall_monotonic_ns": self.clock(),
            "owned_freeze_released": after["freeze_count"] == 0,
            **{key+"_before": value for key, value in (before or {}).items()},
            **{key+"_after": value for key, value in after.items()},
        }
        restored = (after["gc_enabled"] is True
                    and after["thresholds"] == self._original["thresholds"]
                    and after["freeze_count"] == 0)
        if restored:
            self._freeze_attempted = self.owned = self._restore_needed = False
            self._closed = True
        if errors or not restored:
            raise RuntimeError("collector GC ownership/dynamic policy was not restored") from (errors[0] if errors else None)
        return self.profile


CollectorStaticGCProfile = CollectorGCScope
