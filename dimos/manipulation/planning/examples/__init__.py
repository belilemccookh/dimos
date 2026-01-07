# Copyright 2025 Dimensional Inc.
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

"""
Planning Examples and Interactive Tools

## Interactive Tools

```bash
# Local planning tester (standalone, no cluster needed)
python -m dimos.manipulation.planning.examples.planning_tester

# RPC client for deployed ManipulationModule
python -m dimos.manipulation.planning.examples.manipulation_client
```

## Planning Tester Commands

- **Robot Control**: joints, home, random, ee, collision
- **Planning**: ik, plan
- **Obstacles**: add, move, remove, list, clear

## Manipulation Client Commands

- **Query**: state, ee, joints, url
- **Motion**: pose, joint, plan, preview, execute
- **Obstacles**: box, sphere, remove, clear
"""

from dimos.manipulation.planning.examples.manipulation_client import ManipulationClient
from dimos.manipulation.planning.examples.planning_tester import PlanningTester

__all__ = ["ManipulationClient", "PlanningTester"]
