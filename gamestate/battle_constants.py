from enum import IntEnum, IntFlag

class StatusCondition(IntFlag):
    NONE = 0
    SLEEP_MASK = 0b111  # Bits 0-2
    SLEEP = 0b001  # For name display purposes
    POISON = 0b1000  # Bit 3
    BURN = 0b10000  # Bit 4
    FREEZE = 0b100000  # Bit 5
    PARALYSIS = 0b1000000  # Bit 6
    
    @property
    def is_asleep(self) -> bool:
        """Check if the Pokémon is asleep (any value in bits 0-2)"""
        # For sleep, we directly check if any bits in positions 0-2 are set (values 1-7)
        return bool(int(self) & 0b111)
    
    def get_status_name(self) -> str:
        """Get a human-readable status name"""
        if self.is_asleep:
            return "SLEEP"
        elif self & StatusCondition.PARALYSIS:
            return "PARALYSIS"
        elif self & StatusCondition.FREEZE:
            return "FREEZE"
        elif self & StatusCondition.BURN:
            return "BURN"
        elif self & StatusCondition.POISON:
            return "POISON"
        return "OK"


class Move(IntEnum):
    """Maps move IDs to their names"""

    POUND = 0x01
    KARATE_CHOP = 0x02
    DOUBLESLAP = 0x03
    COMET_PUNCH = 0x04
    MEGA_PUNCH = 0x05
    PAY_DAY = 0x06
    FIRE_PUNCH = 0x07
    ICE_PUNCH = 0x08
    THUNDERPUNCH = 0x09
    SCRATCH = 0x0A
    VICEGRIP = 0x0B
    GUILLOTINE = 0x0C
    RAZOR_WIND = 0x0D
    SWORDS_DANCE = 0x0E
    CUT = 0x0F
    GUST = 0x10
    WING_ATTACK = 0x11
    WHIRLWIND = 0x12
    FLY = 0x13
    BIND = 0x14
    SLAM = 0x15
    VINE_WHIP = 0x16
    STOMP = 0x17
    DOUBLE_KICK = 0x18
    MEGA_KICK = 0x19
    JUMP_KICK = 0x1A
    ROLLING_KICK = 0x1B
    SAND_ATTACK = 0x1C
    HEADBUTT = 0x1D
    HORN_ATTACK = 0x1E
    FURY_ATTACK = 0x1F
    HORN_DRILL = 0x20
    TACKLE = 0x21
    BODY_SLAM = 0x22
    WRAP = 0x23
    TAKE_DOWN = 0x24
    THRASH = 0x25
    DOUBLE_EDGE = 0x26
    TAIL_WHIP = 0x27
    POISON_STING = 0x28
    TWINEEDLE = 0x29
    PIN_MISSILE = 0x2A
    LEER = 0x2B
    BITE = 0x2C
    GROWL = 0x2D
    ROAR = 0x2E
    SING = 0x2F
    SUPERSONIC = 0x30
    SONICBOOM = 0x31
    DISABLE = 0x32
    ACID = 0x33
    EMBER = 0x34
    FLAMETHROWER = 0x35
    MIST = 0x36
    WATER_GUN = 0x37
    HYDRO_PUMP = 0x38
    SURF = 0x39
    ICE_BEAM = 0x3A
    BLIZZARD = 0x3B
    PSYBEAM = 0x3C
    BUBBLEBEAM = 0x3D
    AURORA_BEAM = 0x3E
    HYPER_BEAM = 0x3F
    PECK = 0x40
    DRILL_PECK = 0x41
    SUBMISSION = 0x42
    LOW_KICK = 0x43
    COUNTER = 0x44
    SEISMIC_TOSS = 0x45
    STRENGTH = 0x46
    ABSORB = 0x47
    MEGA_DRAIN = 0x48
    LEECH_SEED = 0x49
    GROWTH = 0x4A
    RAZOR_LEAF = 0x4B
    SOLARBEAM = 0x4C
    POISONPOWDER = 0x4D
    STUN_SPORE = 0x4E
    SLEEP_POWDER = 0x4F
    PETAL_DANCE = 0x50
    STRING_SHOT = 0x51
    DRAGON_RAGE = 0x52
    FIRE_SPIN = 0x53
    THUNDERSHOCK = 0x54
    THUNDERBOLT = 0x55
    THUNDER_WAVE = 0x56
    THUNDER = 0x57
    ROCK_THROW = 0x58
    EARTHQUAKE = 0x59
    FISSURE = 0x5A
    DIG = 0x5B
    TOXIC = 0x5C
    CONFUSION = 0x5D
    PSYCHIC = 0x5E
    HYPNOSIS = 0x5F
    MEDITATE = 0x60
    AGILITY = 0x61
    QUICK_ATTACK = 0x62
    RAGE = 0x63
    TELEPORT = 0x64
    NIGHT_SHADE = 0x65
    MIMIC = 0x66
    SCREECH = 0x67
    DOUBLE_TEAM = 0x68
    RECOVER = 0x69
    HARDEN = 0x6A
    MINIMIZE = 0x6B
    SMOKESCREEN = 0x6C
    CONFUSE_RAY = 0x6D
    WITHDRAW = 0x6E
    DEFENSE_CURL = 0x6F
    BARRIER = 0x70
    LIGHT_SCREEN = 0x71
    HAZE = 0x72
    REFLECT = 0x73
    FOCUS_ENERGY = 0x74
    BIDE = 0x75
    METRONOME = 0x76
    MIRROR_MOVE = 0x77
    SELFDESTRUCT = 0x78
    EGG_BOMB = 0x79
    LICK = 0x7A
    SMOG = 0x7B
    SLUDGE = 0x7C
    BONE_CLUB = 0x7D
    FIRE_BLAST = 0x7E
    WATERFALL = 0x7F
    CLAMP = 0x80
    SWIFT = 0x81
    SKULL_BASH = 0x82
    SPIKE_CANNON = 0x83
    CONSTRICT = 0x84
    AMNESIA = 0x85
    KINESIS = 0x86
    SOFTBOILED = 0x87
    HI_JUMP_KICK = 0x88
    GLARE = 0x89
    DREAM_EATER = 0x8A
    POISON_GAS = 0x8B
    BARRAGE = 0x8C
    LEECH_LIFE = 0x8D
    LOVELY_KISS = 0x8E
    SKY_ATTACK = 0x8F
    TRANSFORM = 0x90
    BUBBLE = 0x91
    DIZZY_PUNCH = 0x92
    SPORE = 0x93
    FLASH = 0x94
    PSYWAVE = 0x95
    SPLASH = 0x96
    ACID_ARMOR = 0x97
    CRABHAMMER = 0x98
    EXPLOSION = 0x99
    FURY_SWIPES = 0x9A
    BONEMERANG = 0x9B
    REST = 0x9C
    ROCK_SLIDE = 0x9D
    HYPER_FANG = 0x9E
    SHARPEN = 0x9F
    CONVERSION = 0xA0
    TRI_ATTACK = 0xA1
    SUPER_FANG = 0xA2
    SLASH = 0xA3
    SUBSTITUTE = 0xA4
    STRUGGLE = 0xA5


class Badge(IntFlag):
    """Flags for gym badges"""

    BOULDER = 1 << 0
    CASCADE = 1 << 1
    THUNDER = 1 << 2
    RAINBOW = 1 << 3
    SOUL = 1 << 4
    MARSH = 1 << 5
    VOLCANO = 1 << 6
    EARTH = 1 << 7

Items = {
            0x01: "MASTER BALL",
            0x02: "ULTRA BALL",
            0x03: "GREAT BALL",
            0x04: "POKé BALL",
            0x05: "TOWN MAP",
            0x06: "BICYCLE",
            0x07: "???",
            0x08: "SAFARI BALL",
            0x09: "POKéDEX",
            0x0A: "MOON STONE",
            0x0B: "ANTIDOTE",
            0x0C: "BURN HEAL",
            0x0D: "ICE HEAL",
            0x0E: "AWAKENING",
            0x0F: "PARLYZ HEAL",
            0x10: "FULL RESTORE",
            0x11: "MAX POTION",
            0x12: "HYPER POTION",
            0x13: "SUPER POTION",
            0x14: "POTION",
            # Badges 0x15-0x1C
            0x1D: "ESCAPE ROPE",
            0x1E: "REPEL",
            0x1F: "OLD AMBER",
            0x20: "FIRE STONE",
            0x21: "THUNDERSTONE",
            0x22: "WATER STONE",
            0x23: "HP UP",
            0x24: "PROTEIN",
            0x25: "IRON",
            0x26: "CARBOS",
            0x27: "CALCIUM",
            0x28: "RARE CANDY",
            0x29: "DOME FOSSIL",
            0x2A: "HELIX FOSSIL",
            0x2B: "SECRET KEY",
            0x2C: "???",  # Blank item
            0x2D: "BIKE VOUCHER",
            0x2E: "X ACCURACY",
            0x2F: "LEAF STONE",
            0x30: "CARD KEY",
            0x31: "NUGGET",
            0x32: "PP UP",
            0x33: "POKé DOLL",
            0x34: "FULL HEAL",
            0x35: "REVIVE",
            0x36: "MAX REVIVE",
            0x37: "GUARD SPEC",
            0x38: "SUPER REPEL",
            0x39: "MAX REPEL",
            0x3A: "DIRE HIT",
            0x3B: "COIN",
            0x3C: "FRESH WATER",
            0x3D: "SODA POP",
            0x3E: "LEMONADE",
            0x3F: "S.S. TICKET",
            0x40: "GOLD TEETH",
            0x41: "X ATTACK",
            0x42: "X DEFEND",
            0x43: "X SPEED",
            0x44: "X SPECIAL",
            0x45: "COIN CASE",
            0x46: "OAK's PARCEL",
            0x47: "ITEMFINDER",
            0x48: "SILPH SCOPE",
            0x49: "POKé FLUTE",
            0x4A: "LIFT KEY",
            0x4B: "EXP.ALL",
            0x4C: "OLD ROD",
            0x4D: "GOOD ROD",
            0x4E: "SUPER ROD",
            0x4F: "PP UP",
            0x50: "ETHER",
            0x51: "MAX ETHER",
            0x52: "ELIXER",
            0x53: "MAX ELIXER",
        }