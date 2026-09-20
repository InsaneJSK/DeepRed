"""Compatibility entry point; input and a new output path must be explicit."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autonomous_controller.save_state import main

if __name__ == "__main__":
    main()
