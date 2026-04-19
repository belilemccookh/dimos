# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Hardware round-trip test for Bool and Vector3 with float64->float32 precision checks.

Uses only 2 message types to fit within Arduino Uno's 2KB SRAM.

Requires:
    - Arduino Uno connected via USB
    - nix on PATH

Run:
    uv run python dimos/hardware/arduino/examples/arduino_precision_echo/test_precision_echo_hardware.py
"""

from __future__ import annotations

import struct
import sys
import threading
import time
from typing import Any

from dimos.core.coordination.blueprints import autoconnect
from dimos.core.coordination.module_coordinator import ModuleCoordinator
from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.hardware.arduino.examples.arduino_precision_echo.module import PrecisionEcho
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.std_msgs.Bool import Bool
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

# Float64 values chosen to exercise precision loss on AVR (double = float32).
F64_TEST_VALUES = [
    3.141592653589793,
    2.718281828459045,
    -0.123456789012345,
    1.0000001192092896,
    100000.015625,
    1e-7,
]

BOOL_TESTS = [True, False, True]


class TestHarnessConfig(ModuleConfig):
    pass


class TestHarness(Module):
    """Sends Bool and Vector3 test messages and collects echoes."""

    config: TestHarnessConfig

    bool_out: Out[Bool]
    bool_in: In[Bool]
    vec3_out: Out[Vector3]
    vec3_in: In[Vector3]

    _bool_echoes: list[bool]
    _vec3_echoes: list[tuple[float, float, float]]
    _lock: Any  # threading.Lock
    _done: bool

    @rpc
    def start(self) -> None:
        super().start()
        self._bool_echoes = []
        self._vec3_echoes = []
        self._lock = threading.Lock()
        self._done = False
        self.bool_in.subscribe(self._on_bool)
        self.vec3_in.subscribe(self._on_vec3)
        threading.Thread(target=self._send_all, daemon=True).start()

    def _on_bool(self, msg: Bool) -> None:
        with self._lock:
            self._bool_echoes.append(msg.data)

    def _on_vec3(self, msg: Vector3) -> None:
        with self._lock:
            self._vec3_echoes.append((msg.x, msg.y, msg.z))

    def _send_all(self) -> None:
        time.sleep(2)
        for val in BOOL_TESTS:
            self.bool_out.publish(Bool(data=val))
            time.sleep(0.2)

        vec3_tests = [
            Vector3(F64_TEST_VALUES[0], F64_TEST_VALUES[1], F64_TEST_VALUES[2]),
            Vector3(F64_TEST_VALUES[3], F64_TEST_VALUES[4], F64_TEST_VALUES[5]),
            Vector3(0.0, 0.0, 0.0),
        ]
        for vec in vec3_tests:
            self.vec3_out.publish(vec)
            time.sleep(0.2)

        time.sleep(2)
        with self._lock:
            self._done = True
        logger.info("All test messages sent")

    @rpc
    def get_results(self) -> dict[str, Any]:
        with self._lock:
            return {
                "done": self._done,
                "bool_echoes": list(self._bool_echoes),
                "vec3_echoes": list(self._vec3_echoes),
            }


def float64_to_float32(val: float) -> float:
    return struct.unpack("f", struct.pack("f", val))[0]


def validate_results(results: dict[str, Any]) -> bool:
    passed = True

    print(f"\n{'=' * 60}")
    print("BOOL ECHO TEST")
    print(f"{'=' * 60}")
    bool_echoes = results["bool_echoes"]
    if len(bool_echoes) >= len(BOOL_TESTS):
        for sent, got in zip(BOOL_TESTS, bool_echoes, strict=False):
            status = "OK" if sent == got else "FAIL"
            print(f"  [{status}] sent={sent} got={got}")
            if sent != got:
                passed = False
    else:
        print(f"  [FAIL] Expected {len(BOOL_TESTS)} echoes, got {len(bool_echoes)}")
        passed = False

    print(f"\n{'=' * 60}")
    print("VECTOR3 ECHO TEST (float64 -> float32 precision)")
    print(f"{'=' * 60}")
    vec3_tests = [
        (F64_TEST_VALUES[0], F64_TEST_VALUES[1], F64_TEST_VALUES[2]),
        (F64_TEST_VALUES[3], F64_TEST_VALUES[4], F64_TEST_VALUES[5]),
        (0.0, 0.0, 0.0),
    ]
    vec3_echoes = results["vec3_echoes"]
    if len(vec3_echoes) >= len(vec3_tests):
        for i, (sent, got) in enumerate(zip(vec3_tests, vec3_echoes, strict=False)):
            for axis, (s, g) in zip("xyz", zip(sent, got, strict=False), strict=False):
                expected_f32 = float64_to_float32(s)
                abs_err = abs(g - expected_f32)
                tol = max(abs(expected_f32) * 1.2e-7, 1e-45)
                status = "OK" if abs_err <= tol else "FAIL"
                precision_lost = abs(s - g)
                print(
                    f"  [{status}] vec[{i}].{axis}: "
                    f"sent_f64={s:.15g}  expected_f32={expected_f32:.8g}  "
                    f"got={g:.8g}  err_vs_f32={abs_err:.2e}  "
                    f"total_precision_loss={precision_lost:.2e}"
                )
                if abs_err > tol:
                    passed = False
    else:
        print(f"  [FAIL] Expected {len(vec3_tests)} echoes, got {len(vec3_echoes)}")
        passed = False

    print(f"\n{'=' * 60}")
    print("ALL TESTS PASSED" if passed else "SOME TESTS FAILED")
    print(f"{'=' * 60}")
    return passed


def main() -> None:
    bp = (
        autoconnect(
            TestHarness.blueprint(),
            PrecisionEcho.blueprint(virtual=False),
        )
        .remappings(
            [
                (TestHarness, "bool_out", "bool_cmd"),
                (PrecisionEcho, "bool_in", "bool_cmd"),
                (PrecisionEcho, "bool_out", "bool_echo"),
                (TestHarness, "bool_in", "bool_echo"),
                (TestHarness, "vec3_out", "vec3_cmd"),
                (PrecisionEcho, "vec3_in", "vec3_cmd"),
                (PrecisionEcho, "vec3_out", "vec3_echo"),
                (TestHarness, "vec3_in", "vec3_echo"),
            ]
        )
        .global_config(n_workers=2)
    )

    coord = ModuleCoordinator.build(bp)
    harness = coord.get_instance(TestHarness)
    assert harness is not None

    deadline = time.time() + 60
    results: dict[str, Any] = {}
    while time.time() < deadline:
        results = harness.get_results()
        if results.get("done"):
            break
        time.sleep(1)
    coord.stop()

    if not results.get("done"):
        print("FAIL: Test harness did not finish within 60 seconds")
        sys.exit(1)

    ok = validate_results(results)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
