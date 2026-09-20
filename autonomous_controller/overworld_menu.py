"""Observed overworld choices, separate from text advancement and battle menus."""

from pyboy.utils import WindowEvent as E

from autonomous_controller.emulator_session import FrameBudget
from autonomous_controller.interrupt_handler import BattleInterrupt
from memory_state.battle_constants import ITEMS


class OverworldMenu:
    def __init__(self, emulator, state):
        self.emulator = emulator
        self.gs = state
        self.quantity_confirmed = False

    def observe(self):
        read = self.gs.mem.read_byte
        text = self.gs.dialog.upper()
        pointer = read(0xCC30) + 256 * read(0xCC31)
        arrow = 0xC3A0 <= pointer < 0xC508 and read(pointer) == 0xED
        x, y = read(0xCC25), read(0xCC24)
        kind, options = None, []
        if arrow:
            self.quantity_confirmed = False
            if (x, y) == (1, 1) and "BUY" in text and "QUIT" in text:
                kind, options = "shop", ["Buy", "Sell", "Quit"]
            elif (x, y) == (12, 8) and "HEAL" in text and "CANCEL" in text:
                kind, options = "heal", ["Heal", "Cancel"]
            elif "YES" in text and "NO" in text and read(0xCC28) == 1:
                kind, options = "yes_no", ["Yes", "No"]
            elif (x, y) == (5, 4) and read(0xCF94) in (2, 3):
                address = read(0xCF8B) + 256 * read(0xCF8C)
                if address == 0xCF7B:
                    kind = "buy"
                    options = [
                        ITEMS.get(read(address + 1 + i), "Unknown item")
                        for i in range(min(read(address), 20))
                    ]
                elif address == 0xD31D:
                    kind, options = "sell", [name for name, _ in self.gs.items]
                if kind:
                    options.append("Cancel")
            if not kind:
                return {"kind": "unsupported", "options": []}
        elif (
            not self.quantity_confirmed
            and (x, y) == (5, 4)
            and read(0xC3A0 + 10 * 20 + 8) == 0xF1
            and ("TAKE YOUR TIME" in text or "LIKE TO SELL?" in text)
        ):
            return {
                "kind": "quantity",
                "current": read(0xCF96),
                "maximum": read(0xCF97),
                "options": [],
            }
        if kind:
            cursor = read(0xCC26) + (read(0xCC36) if kind in ("buy", "sell") else 0)
            return {
                "kind": kind,
                "cursor": cursor,
                "options": [{"index": i, "name": label} for i, label in enumerate(options)],
            }
        return None

    @staticmethod
    def press(budget, button, hold=2, settle=8):
        budget.press(getattr(E, "PRESS_" + button), getattr(E, "RELEASE_" + button), hold, settle)

    def choose(self, option_index):
        menu = self.observe()
        if not menu or not 0 <= option_index < len(menu["options"]):
            raise ValueError("Choose one of the listed menu options")
        budget = FrameBudget(self.emulator, 1200)
        budget.tick(8)
        for _ in range(50):
            current = self.observe()
            if not current or current["kind"] != menu["kind"]:
                raise RuntimeError("Menu changed while choosing")
            if current["cursor"] == option_index:
                self.press(budget, "BUTTON_A")
                budget.tick(20)
                return True
            self.press(budget, "ARROW_DOWN" if current["cursor"] < option_index else "ARROW_UP")
        raise RuntimeError("Menu cursor did not reach the requested option")

    def quantity(self, amount):
        menu = self.observe()
        if not menu or menu["kind"] != "quantity" or not 1 <= amount <= menu["maximum"]:
            raise ValueError("Quantity must be between 1 and the displayed maximum")
        budget = FrameBudget(self.emulator, 2000)
        for _ in range(100):
            current = self.gs.mem.read_byte(0xCF96)
            if current == amount:
                self.quantity_confirmed = True
                self.press(budget, "BUTTON_A")
                return True
            self.press(budget, "ARROW_UP" if current < amount else "ARROW_DOWN")
        raise RuntimeError("Quantity did not reach the requested amount")

    def cancel(self):
        self.quantity_confirmed = True
        budget = FrameBudget(self.emulator, 60)
        budget.tick(8)
        self.press(budget, "BUTTON_B", settle=30)
        return True

    def advance(self):
        budget = FrameBudget(self.emulator, 6000)
        clear = cooldown = 0
        while budget.remaining:
            if self.gs.map["in_battle"]:
                if self.gs.mem.read_byte(0xD05A) != 1:
                    raise BattleInterrupt("Battle started")
                if cooldown == 0:
                    self.press(budget, "BUTTON_B", hold=1, settle=0)
                    cooldown = 20
                budget.tick()
                cooldown = max(0, cooldown - 1)
                continue
            menu = self.observe()
            if menu and "GIVE A NICKNAME TO" not in self.gs.dialog.upper():
                return True
            if self.gs.dialog.strip():
                clear = 0
                if cooldown == 0:
                    self.press(budget, "BUTTON_B", hold=1, settle=0)
                    cooldown = 20
            else:
                clear += 1
                if clear >= 120:
                    return True
            budget.tick()
            cooldown = max(0, cooldown - 1)
        return False
