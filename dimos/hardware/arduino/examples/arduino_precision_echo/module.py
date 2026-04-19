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

"""Precision echo ArduinoModule for hardware round-trip testing.

Echoes Bool and Vector3 messages to validate serialization correctness
and float64->float32 precision on AVR.  Kept to 2 message types so the
sketch fits within Arduino Uno's 2KB SRAM.
"""

from __future__ import annotations

from dimos.core.arduino_module import ArduinoModule, ArduinoModuleConfig
from dimos.core.stream import In, Out
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.std_msgs.Bool import Bool


class PrecisionEchoConfig(ArduinoModuleConfig):
    sketch_path: str = "sketch/sketch.ino"
    board_fqbn: str = "arduino:avr:uno"
    baudrate: int = 115200


class PrecisionEcho(ArduinoModule):
    """Arduino that echoes Bool and Vector3 messages back."""

    config: PrecisionEchoConfig

    bool_in: In[Bool]
    bool_out: Out[Bool]

    vec3_in: In[Vector3]
    vec3_out: Out[Vector3]
