"""Create a separate manual-testing checkpoint without replacing existing saves."""

import argparse
from pathlib import Path

from autonomous_controller.emulator_session import open_emulator


def create_checkpoint(rom, source, destination, frames=600):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or destination.exists():
        raise ValueError("Choose a new output file; existing saves will not be overwritten")
    if frames < 0:
        raise ValueError("frames must be non-negative")
    with source.open("rb") as saved:
        with open_emulator(rom, window="SDL2") as emulator:
            emulator.load_state(saved)
            for _ in range(frames):
                emulator.tick()
            # Exclusive creation also protects against another writer during the run.
            with destination.open("xb") as output:
                emulator.save_state(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", default="Pokemon_Red/Red.gb")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--frames", type=int, default=600)
    args = parser.parse_args()
    create_checkpoint(args.rom, args.input, args.output, args.frames)


if __name__ == "__main__":
    main()
