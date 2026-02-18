"""
Provides utilities for reading memory from the PyBoy emulator.
"""
class MemoryReader:
    """
    Thin wrapper over PyBoy memory access.
    Handles bytes, words, and sequences.
    """

    def __init__(self, state):
        self.pyboy = state

    def read_byte(self, addr: int) -> int:
        """Read a single byte from memory."""
        return self.pyboy.memory[addr]

    def read_bytes(self, addr: int, length: int):
        """Read a sequence of bytes from memory."""
        return [self.pyboy.memory[addr + i] for i in range(length)]

    def read_word(self, addr: int) -> int:
        """Read a 16-bit word from memory"""
        lo = self.pyboy.memory[addr]
        hi = self.pyboy.memory[addr + 1]
        return lo | (hi << 8)

CHAR_MAP = {
    #Line Break
    0x4E: "\n",
    #Punctuations
    0x9A: "(",
    0x9B: ")",
    0x9C: ":",
    0x9D: ";",
    0x9E: "[",
    0x9F: "]",
    # Special Characters
    0x7F: " ",
    0x6D: ":",
    0x54: "POKé",
    0xBA: "é",
    0xBB: "'d",
    0xBC: "'l",
    0xBD: "'s",
    0xBE: "'t",
    0xBF: "'v",
    0xE0: "'",
    0xE1: "Pk",
    0xE2: "Mn",
    0xE3: "-",
    0xE4: "'r",
    0xE5: "'m",
    0xE6: "?",
    0xE7: "!",
    0xE8: ".",
    0xE9: "ア",
    0xEA: "ウ",
    0xEB: "エ",
    0xEC: "▷",
    0xED: "►",
    0xEE: "▼",
    0xEF: "♂",
    0xF0: "♭",
    0xF1: "×",
    0xF2: ".",
    0xF3: "/",
    0xF4: ",",
    0xF5: "♀",
}

def convert_text(bytes_data: list[int]) -> str:
    """Convert Pokemon text format to ASCII"""
    result = []
    for b in bytes_data:
        if b == 0x50:  # End marker
            break

        #Alphabet Ranges
        if 0x80 <= b <= 0x99:  # A-Z
            result.append(chr(b - 0x80 + ord("A")))
        elif 0xA0 <= b <= 0xB9:  # a-z
            result.append(chr(b - 0xA0 + ord("a")))
        elif 0xF6 <= b <= 0xFF:  # Numbers 0-9
            result.append(str(b - 0xF6))
        elif 0xF6 <= b <= 0xFF:
            result.append(str(b - 0xF6))
        else:
            result.append(CHAR_MAP.get(b, f"[{b:02X}]"))
    return "".join(result).strip()

if __name__ == "__main__":
    from pyboy import PyBoy
    pyboy_sample = PyBoy("Pokemon_Red/Red.gb")
    m = MemoryReader(pyboy_sample)

    print(hex(m.read_byte(0xD000)))
