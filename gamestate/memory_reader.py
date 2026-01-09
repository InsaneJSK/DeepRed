class MemoryReader:
    """
    Thin wrapper over PyBoy memory access.
    Handles bytes, words, and sequences.
    """

    def __init__(self, pyboy):
        self.pyboy = pyboy

    def read_byte(self, addr: int) -> int:
        return self.pyboy.memory[addr]

    def read_bytes(self, addr: int, length: int):
        return [self.pyboy.memory[addr + i] for i in range(length)]

    def read_word(self, addr: int) -> int:
        lo = self.pyboy.memory[addr]
        hi = self.pyboy.memory[addr + 1]
        return lo | (hi << 8)

if __name__ == "__main__":
    from pyboy import PyBoy
    pyboy = PyBoy("Pokemon_Red/Red.gb")
    m = MemoryReader(pyboy)

    print(hex(m.read_byte(0xD000)))

def convert_text(bytes_data: list[int]) -> str:
        """Convert Pokemon text format to ASCII"""
        result = ""
        for b in bytes_data:
            if b == 0x50:  # End marker
                break
            elif b == 0x4E:  # Line break
                result += "\n"
            # Main character ranges
            elif 0x80 <= b <= 0x99:  # A-Z
                result += chr(b - 0x80 + ord("A"))
            elif 0xA0 <= b <= 0xB9:  # a-z
                result += chr(b - 0xA0 + ord("a"))
            elif 0xF6 <= b <= 0xFF:  # Numbers 0-9
                result += str(b - 0xF6)
            # Punctuation characters (9A-9F)
            elif b == 0x9A:  # (
                result += "("
            elif b == 0x9B:  # )
                result += ")"
            elif b == 0x9C:  # :
                result += ":"
            elif b == 0x9D:  # ;
                result += ";"
            elif b == 0x9E:  # [
                result += "["
            elif b == 0x9F:  # ]
                result += "]"
            # Special characters
            elif b == 0x7F:  # Space
                result += " "
            elif b == 0x6D:  # : (also appears here)
                result += ":"
            elif b == 0x54:  # POKé control character
                result += "POKé"
            elif b == 0xBA:  # é
                result += "é"
            elif b == 0xBB:  # 'd
                result += "'d"
            elif b == 0xBC:  # 'l
                result += "'l"
            elif b == 0xBD:  # 's
                result += "'s"
            elif b == 0xBE:  # 't
                result += "'t"
            elif b == 0xBF:  # 'v
                result += "'v"
            elif b == 0xE1:  # PK
                result += "Pk"
            elif b == 0xE2:  # MN
                result += "Mn"
            elif b == 0xE3:  # -
                result += "-"
            elif b == 0xE6:  # ?
                result += "?"
            elif b == 0xE7:  # !
                result += "!"
            elif b == 0xE8:  # .
                result += "."
            elif b == 0xE9:  # .
                result += "."
            # E-register special characters
            elif b == 0xE0:  # '
                result += "'"
            elif b == 0xE1:  # PK
                result += "POKé"
            elif b == 0xE2:  # MN
                result += "MON"
            elif b == 0xE3:  # -
                result += "-"
            elif b == 0xE4:  # 'r
                result += "'r"
            elif b == 0xE5:  # 'm
                result += "'m"
            elif b == 0xE6:  # ?
                result += "?"
            elif b == 0xE7:  # !
                result += "!"
            elif b == 0xE8:  # .
                result += "."
            elif b == 0xE9:  # ア
                result += "ア"
            elif b == 0xEA:  # ウ
                result += "ウ"
            elif b == 0xEB:  # エ
                result += "エ"
            elif b == 0xEC:  # ▷
                result += "▷"
            elif b == 0xED:  # ►
                result += "►"
            elif b == 0xEE:  # ▼
                result += "▼"
            elif b == 0xEF:  # ♂
                result += "♂"
            # F-register special characters
            elif b == 0xF0:  # ♭
                result += "♭"
            elif b == 0xF1:  # ×
                result += "×"
            elif b == 0xF2:  # .
                result += "."
            elif b == 0xF3:  # /
                result += "/"
            elif b == 0xF4:  # ,
                result += ","
            elif b == 0xF5:  # ♀
                result += "♀"
            # Numbers 0-9 (0xF6-0xFF)
            elif 0xF6 <= b <= 0xFF:
                result += str(b - 0xF6)
            else:
                # For debugging, show the hex value of unknown characters
                result += f"[{b:02X}]"
        return result.strip()