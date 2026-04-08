"""
autonomous_controller/__init__.py

Re-exports AutonomousController for back-compat:
    from autonomous_controller.controller import AutonomousController
or:
    from autonomous_controller import AutonomousController
"""

from autonomous_controller.controller import AutonomousController
from autonomous_controller.interrupt_handler import BattleInterrupt

__all__ = ["AutonomousController", "BattleInterrupt"]
