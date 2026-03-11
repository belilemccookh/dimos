"""Registry of available DUI sub-apps."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dimos.utils.cli.dui.sub_app import SubApp


def get_sub_apps() -> list[type[SubApp]]:
    """Return all available sub-app classes in display order."""
    # from dimos.utils.cli.dui.sub_apps.agentspy import AgentSpySubApp
    from dimos.utils.cli.dui.sub_apps.config import ConfigSubApp
    from dimos.utils.cli.dui.sub_apps.dtop import DtopSubApp
    from dimos.utils.cli.dui.sub_apps.humancli import HumanCLISubApp
    from dimos.utils.cli.dui.sub_apps.launcher import LauncherSubApp
    from dimos.utils.cli.dui.sub_apps.lcmspy import LCMSpySubApp
    from dimos.utils.cli.dui.sub_apps.runner import StatusSubApp

    return [
        LauncherSubApp,
        StatusSubApp,
        ConfigSubApp,
        DtopSubApp,
        LCMSpySubApp,
        HumanCLISubApp,
        # AgentSpySubApp,
    ]
