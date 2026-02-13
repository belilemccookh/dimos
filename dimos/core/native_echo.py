#!/usr/bin/env python3
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

"""Echo binary for NativeModule tests.

Dumps CLI args as a JSON log line to stdout, then waits for SIGTERM.

Env vars:
    NATIVE_ECHO_OUTPUT: path to write CLI args as JSON
    NATIVE_ECHO_DIE_AFTER: seconds to wait before exiting with code 42
"""

import json
import os
import signal
import sys
import time

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

output_path = os.environ.get("NATIVE_ECHO_OUTPUT")
if output_path:
    with open(output_path, "w") as f:
        json.dump(sys.argv[1:], f)
else:
    json.dump({"event": "echo_args", "args": sys.argv[1:]}, sys.stdout)
    sys.stdout.write("\n")
    sys.stdout.flush()

die_after = os.environ.get("NATIVE_ECHO_DIE_AFTER")
if die_after:
    time.sleep(float(die_after))
    sys.exit(42)

signal.pause()
