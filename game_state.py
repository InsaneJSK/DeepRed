from gamestate.memory_reader import MemoryReader, convert_text
from gamestate.map_constants import MapLocation, Tileset
from gamestate.pokemon_constants import Pokemon, PokemonType
from gamestate.battle_constants import Badge, StatusCondition, Move, Items

MAP_ID_ADDR = 0xD35E
PLAYER_X_ADDR = 0xD361
PLAYER_Y_ADDR = 0xD362
PLAYER_FACING_ADDR = 0xC109
BATTLE_FLAG_ADDR = 0xD057


class PokemonGameState:
    """Reads and interprets memory values from Pokemon Red"""
    def __init__(self, pyboy):
        """Initialize with a PyBoy memory view object"""
        self.pyboy = pyboy
        self.mem = MemoryReader(pyboy)

    @property
    def map_id(self) -> int:
        return self.mem.read_byte(MAP_ID_ADDR)

    @property
    def player_x(self) -> int:
        return self.mem.read_byte(PLAYER_X_ADDR)

    @property
    def player_y(self) -> int:
        return self.mem.read_byte(PLAYER_Y_ADDR)

    @property
    def map_name(self) -> str:
        return MapLocation(self.map_id).name.replace("_", " ").title()
    
    @property
    def player_facing(self) -> str:
        val = self.mem.read_byte(PLAYER_FACING_ADDR)
        directions = {
            0x0: "DOWN",
            0x4: "UP",
            0x8: "LEFT",
            0xc: "RIGHT",
        }
        return directions.get(val, f"UNKNOWN({val})")
    
    @property
    def in_battle(self) -> bool:
        return bool(self.mem.read_byte(BATTLE_FLAG_ADDR))
    
    @property
    def money(self) -> int:
        """Read the player's money in Binary Coded Decimal format"""
        b1 = self.mem.read_byte(0xD349)  # Least significant byte
        b2 = self.mem.read_byte(0xD348)  # Middle byte
        b3 = self.mem.read_byte(0xD347)  # Most significant byte
        amount = (
            ((b3 >> 4) * 100000)
            + ((b3 & 0xF) * 10000)
            + ((b2 >> 4) * 1000)
            + ((b2 & 0xF) * 100)
            + ((b1 >> 4) * 10)
            + (b1 & 0xF)
        )
        return amount

    @property
    def player_name(self) -> str:
        """Read the player's name"""
        name_bytes = self.mem.read_bytes(0xD158, 0xD163)
        return convert_text(name_bytes)
    
    @property
    def rival_name(self) -> str:
        """Read rival's name"""
        name_bytes = self.mem.read_bytes(0xD34A, 0x07)
        return convert_text(name_bytes)

    @property
    def badges(self) -> list[str]:
        """Read obtained badges as list of names"""
        badge_byte = self.mem.read_byte(0xD356)
        badges = []

        if badge_byte & Badge.BOULDER:
            badges.append("BOULDER")
        if badge_byte & Badge.CASCADE:
            badges.append("CASCADE")
        if badge_byte & Badge.THUNDER:
            badges.append("THUNDER")
        if badge_byte & Badge.RAINBOW:
            badges.append("RAINBOW")
        if badge_byte & Badge.SOUL:
            badges.append("SOUL")
        if badge_byte & Badge.MARSH:
            badges.append("MARSH")
        if badge_byte & Badge.VOLCANO:
            badges.append("VOLCANO")
        if badge_byte & Badge.EARTH:
            badges.append("EARTH")

        return badges

    def read_party_size(self) -> int:
        """Read number of Pokemon in party"""
        return self.mem.read_byte(0xD163)
    
    @property
    def party_pokemon(self) -> list[dict]:
        """Read all Pokemon currently in the party with full data"""
        party = []
        party_size = self.read_party_size()

        # Base addresses for party Pokemon data
        base_addresses = [0xD16B, 0xD197, 0xD1C3, 0xD1EF, 0xD21B, 0xD247]
        nickname_addresses = [0xD2B5, 0xD2C0, 0xD2CB, 0xD2D6, 0xD2E1, 0xD2EC]

        for i in range(party_size):
            addr = base_addresses[i]

            # Read experience (3 bytes)
            exp = (
                (self.mem.read_byte(addr + 0x1A) << 16)
                + (self.mem.read_byte(addr + 0x1B) << 8)
                + self.mem.read_byte(addr + 0x1C)
            )

            # Read moves and PP
            moves = []
            move_pp = []
            for j in range(4):
                move_id = self.mem.read_byte(addr + 8 + j)
                if move_id != 0:
                    moves.append(Move(move_id).name.replace("_", " "))
                    move_pp.append(self.mem.read_byte(addr + 0x1D + j))

            # Read nickname
            nickname = convert_text(
                self.mem.read_bytes(nickname_addresses[i], nickname_addresses[i] + 11)
            )

            type1 = PokemonType(self.mem.read_byte(addr + 5))
            type2 = PokemonType(self.mem.read_byte(addr + 6))
            # If both types are the same, only show one type
            if type1 == type2:
                type2 = None

            try:
                species_id = self.mem.read_byte(addr)
                species_name = Pokemon(species_id).name.replace("_", " ")
            except ValueError:
                continue
            status_value = self.mem.read_byte(addr + 4)
            
            pokemon = {
                "species_name": species_name,
                "current_hp": f"{(self.mem.read_byte(addr + 1) << 8) + self.mem.read_byte(addr + 2)}/{(self.mem.read_byte(addr + 0x22) << 8) + self.mem.read_byte(addr + 0x23)}",
                "level": self.mem.read_byte(addr + 0x21),  # Using actual level
                "status": StatusCondition(status_value).get_status_name(),
                "type1": type1.name.replace("_", " "),
                "type2": type2.name.replace("_", " ") if type2 else None,
                "moves, pp": list(zip(moves, move_pp)),
                "trainer_id": (self.mem.read_byte(addr + 12) << 8) + self.mem.read_byte(addr + 13),
                "nickname": nickname,
                "experience": exp,
            }
            party.append(pokemon)

        return party

    @property
    def tileset(self) -> str:
        """Read current map's tileset name"""
        tileset_id = self.mem.read_byte(0xD367)
        return Tileset(tileset_id).name.replace("_", " ")

    @property
    def read_coins(self) -> int:
        """Read game corner coins"""
        return (self.mem.read_byte(0xD5A4) << 8) + self.mem.read_byte(0xD5A5)

    def read_item_count(self) -> int:
        """Read number of items in inventory"""
        return self.mem.read_byte(0xD31D)

    @property
    def items(self) -> list[tuple[str, int]]:
        """Read all items in inventory with proper item names"""
        ITEM_NAMES = Items

        item_list = []
        count = self.read_item_count()

        for i in range(count):
            item_id = self.mem.read_byte(0xD31E + (i * 2))
            quantity = self.mem.read_byte(0xD31F + (i * 2))

            # Handle TMs (0xC9-0xFE)
            if 0xC9 <= item_id <= 0xFE:
                tm_num = item_id - 0xC8
                item_name = f"TM{tm_num:02d}"
            elif 0xC4 <= item_id <= 0xC8:
                hm_num = item_id - 0xC3
                item_name = f"HM{hm_num:02d}"
            elif item_id in ITEM_NAMES:
                item_name = ITEM_NAMES[item_id]
            else:
                item_name = f"UNKNOWN_{item_id:02X}"

            item_list.append((item_name, quantity))

        return item_list

    @property
    def dialog(self) -> str:
        """Read any dialog text currently on screen by scanning the tilemap buffer"""
        # Tilemap buffer is from C3A0 to C507
        buffer_start = 0xC3A0
        buffer_end = 0xC507

        # Get all bytes from the buffer
        buffer_bytes = [self.mem.read_byte(addr) for addr in range(buffer_start, buffer_end)]

        # Look for sequences of text (ignoring long sequences of 0x7F/spaces)
        text_lines = []
        current_line = []
        space_count = 0
        last_was_border = False

        for b in buffer_bytes:
            if b == 0x7C:  # ║ character
                if last_was_border:
                    # If the last character was a border and this is ║, treat as newline
                    text = convert_text(current_line)
                    if text.strip():
                        text_lines.append(text)
                    current_line = []
                    space_count = 0
                else:
                    # current_line.append(b)
                    pass
                last_was_border = True
            elif b == 0x7F:  # Space
                space_count += 1
                current_line.append(b)  # Always keep spaces
                last_was_border = False
            # All text characters: uppercase, lowercase, special chars, punctuation, symbols
            elif (
                # Box drawing (0x79-0x7E)
                # (0x79 <= b <= 0x7E)
                # or
                # Uppercase (0x80-0x99)
                (0x80 <= b <= 0x99)
                or
                # Punctuation (0x9A-0x9F)
                (0x9A <= b <= 0x9F)
                or
                # Lowercase (0xA0-0xB9)
                (0xA0 <= b <= 0xB9)
                or
                # Contractions (0xBA-0xBF)
                (0xBA <= b <= 0xBF)
                or
                # Special characters in E-row (0xE0-0xEF)
                (0xE0 <= b <= 0xEF)
                or
                # Special characters in F-row (0xF0-0xF5)
                (0xF0 <= b <= 0xF5)
                or
                # Numbers (0xF6-0xFF)
                (0xF6 <= b <= 0xFF)
                or
                # Line break
                b == 0x4E
            ):
                space_count = 0
                current_line.append(b)
                last_was_border = (
                    0x79 <= b <= 0x7E
                )  # Track if this is a border character

            # If we see a lot of spaces, might be end of line
            if space_count > 10 and current_line:
                text = convert_text(current_line)
                if text.strip():  # Only add non-empty lines
                    text_lines.append(text)
                current_line = []
                space_count = 0
                last_was_border = False

        # Add final line if any
        if current_line:
            text = convert_text(current_line)
            if text.strip():
                text_lines.append(text)

        text = "\n".join(text_lines)

        # Post-process for name entry context
        if "lower case" in text.lower() or "UPPER CASE" in text:
            # We're in name entry, replace ♭ with ED
            text = text.replace("♭", "ED\n")

        return text

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
        return {
            # "map_id": self.map_id,
            "map_name": self.map_name,
            "tileset": self.tileset,
            "player": {
                "x": self.player_x,
                "y": self.player_y,
                "facing": self.player_facing,
                "money": self.money,
                "party": self.party_pokemon,
                "items": self.items,
            },            
               "status": {
                "in_battle": self.in_battle,
                "badges": self.badges,
            },
            "misc": {
                "player_name": self.player_name,
                "rival_name": self.rival_name,
                "coins": self.read_coins,
                "pokedex_caught": self.pokedex_caught_count,
                "dialog": self.dialog,
            }
        }
    
    def __repr__(self):
        return f"<PokemonGameState> {self.to_dict()}>"
    
    def __str__(self):
        return self.__repr__()
    
    def pretty_print(self):
        import pprint
        pp = pprint.PrettyPrinter(indent=2)
        pp.pprint(self.to_dict())

if __name__ == "__main__":
    from pyboy import PyBoy
    import keyboard

    pyboy = PyBoy("Pokemon_Red/Red.gb", window="SDL2")

    # load your save in the room
    with open("saves/in-room-start.state", "rb") as f:
        pyboy.load_state(f)

    state = PokemonGameState(pyboy)

    print(state.to_dict())
    print("Press ESC to quit.")
    ctr = 0
    while not keyboard.is_pressed("esc"):
        pyboy.tick()
        ctr+=1
        if ctr % 600 == 0:
            print(state.pretty_print())
    pyboy.stop()
