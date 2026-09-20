"""Appearance labels; shared sprites do not identify a unique character.

IDs follow pret/pokered constants/sprite_constants.asm. The map-local old-man
override follows data/maps/objects/ViridianCity.asm. No source checkout is needed.
"""

SPRITE_NAMES = (
    "None",
    "Red",
    "Blue",
    "Professor Oak",
    "Youngster",
    "Monster",
    "Cooltrainer F",
    "Cooltrainer M",
    "Little Girl",
    "Bird",
    "Middle Aged Man",
    "Gambler",
    "Super Nerd",
    "Girl",
    "Hiker",
    "Beauty",
    "Gentleman",
    "Daisy",
    "Biker",
    "Sailor",
    "Cook",
    "Bike Shop Clerk",
    "Mr Fuji",
    "Giovanni",
    "Rocket",
    "Channeler",
    "Waiter",
    "Silph Worker F",
    "Middle Aged Woman",
    "Brunette Girl",
    "Lance",
    "Unused Scientist",
    "Scientist",
    "Rocker",
    "Swimmer",
    "Safari Zone Worker",
    "Gym Guide",
    "Gramps",
    "Clerk",
    "Fishing Guru",
    "Granny",
    "Nurse",
    "Link Receptionist",
    "Silph President",
    "Silph Worker M",
    "Warden",
    "Captain",
    "Fisher",
    "Koga",
    "Guard",
    "Unused Guard",
    "Mom",
    "Balding Guy",
    "Little Boy",
    "Unused Gameboy Kid",
    "Gameboy Kid",
    "Fairy",
    "Agatha",
    "Bruno",
    "Lorelei",
    "Seel",
    "Item ball",
    "Fossil",
    "Boulder",
    "Paper",
    "Pokedex",
    "Clipboard",
    "Snorlax",
    "Unused Old Amber",
    "Old Amber",
    "Unused Gambler Asleep 1",
    "Unused Gambler Asleep 2",
    "Gambler Asleep",
)


def sprite_name(picture_id):
    if 0 <= picture_id < len(SPRITE_NAMES):
        return SPRITE_NAMES[picture_id]
    return f"Unknown sprite {picture_id}"


def object_name(map_key, obj):
    if map_key == "VIRIDIAN_CITY" and obj["slot"] in (5, 7):
        return "Old man (catching tutorial)"
    return sprite_name(obj["picture_id"])
