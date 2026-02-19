"""
Module to read miscellaneous game information like player names, coins, and pokedex count.
"""

import pprint
from memory_state.memory_reader import MemoryReader, convert_text

class MiscInfo:
    """Class to read miscellaneous game information like player names, coins, and pokedex count."""
    def __init__(self, pyby):
        """Initialize with a PyBoy memory view object"""
        self.pyboy = pyby
        self.mem = MemoryReader(pyby)

    @property
    def names(self) -> tuple[str, str]:
        """Read the player's and rival's name"""
        player_name = convert_text(self.mem.read_bytes(0xD158, 0xD163))
        rival_name = convert_text(self.mem.read_bytes(0xD34A, 0x07))
        return player_name, rival_name

    @property
    def read_coins(self) -> int:
        """Read game corner coins"""
        return (self.mem.read_byte(0xD5A4) << 8) + self.mem.read_byte(0xD5A5)

    @property
    def pokedex_caught_count(self) -> int:
        """Read how many unique Pokemon species have been caught"""
        # Pokedex owned flags are stored in D2F7-D309
        # Each byte contains 8 flags for 8 Pokemon
        # Total of 19 bytes = 152 Pokemon
        caught_count = 0
        for addr in range(0xD2F7, 0xD30A):
            byte = self.mem.read_byte(addr)
            # Count set bits in this byte
            caught_count += bin(byte).count("1")
        return caught_count

    def to_dict(self):
        """Convert the misc info to a dictionary for easy printing/logging"""
        return {
            "player_name": self.names[0],
            "rival_name": self.names[1],
            "coins": self.read_coins,
            "pokedex_caught": self.pokedex_caught_count,
        }

    def __repr__(self):
        return f"<MiscInfo> {self.to_dict()}>"

    def __str__(self):
        return self.__repr__()

    def pretty_print(self):
        """Pretty print the game state dictionary"""
        pp = pprint.PrettyPrinter(indent=2)
        pp.pprint(self.to_dict())
