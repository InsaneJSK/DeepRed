"""Readable model choices and a game-plus-history window for recording."""

import time
from collections import deque
from pathlib import Path
from textwrap import wrap

from PIL import Image, ImageDraw, ImageFont

from autonomous_controller.emulator_session import EmulatorClosed


def decision_text(action, observation):
    def readable(value):
        return str(value).replace("_", " ").title()

    def indexed(items, index, fallback):
        return next(
            (
                item.get("name", item.get("nickname", fallback))
                for item in items
                if item.get("index", item.get("slot")) == index
            ),
            fallback,
        )

    name = action.get("action")
    if name == "navigate":
        return "Go to " + readable(action.get("destination", "?"))
    if name == "choose_starter":
        return "Choose " + readable(action.get("pokemon", "?"))
    if name == "fight":
        moves = (observation.get("battle", {}).get("player") or {}).get("moves", [])
        return "Use " + readable(indexed(moves, action.get("move_index"), "move ?"))
    if name == "interact":
        return "Interact with " + readable(
            indexed(observation.get("objects", []), action.get("object_slot"), "object ?")
        )
    if name == "choose":
        return "Choose " + readable(
            indexed(
                (observation.get("menu") or {}).get("options", []),
                action.get("option_index"),
                "option ?",
            )
        )
    if name == "quantity":
        return f"Set quantity to {action.get('amount', '?')}"
    if name == "switch":
        return "Send out " + readable(
            indexed(observation.get("party", []), action.get("party_index"), "Pokemon ?")
        )
    if name == "use_item":
        return "Use " + readable(
            indexed(observation.get("inventory", []), action.get("bag_index"), "item ?")
        )
    if name == "finish":
        return "Finish: " + " ".join(str(action.get("reason", "")).split())[:160]
    return {
        "run": "Try to flee",
        "cancel": "Back out of the menu",
        "resume": "Continue the journey",
        "advance": "Continue",
    }.get(name, str(name))


class DecisionWindow:
    """Render outside emulated pixels; the model never receives this image."""

    def __init__(self, session, goal, model, limit):
        import tkinter as tk

        from PIL import ImageTk

        self.session, self.goal, self.model, self.limit = session, goal, model, limit
        self.history = deque(maxlen=5)
        self.current, self.status, self.destination = "Waiting for a decision", "Starting", ""
        self.calls, self.closed, self.last_draw = 0, False, 0.0
        self.actor = "AI CHOSE"
        self.fast = False
        self.root = tk.Tk()
        self.root.title("DeepRed — AI plays Pokemon Red")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<KeyRelease-space>", self.toggle_speed)
        self.label = tk.Label(self.root, borderwidth=0)
        self.label.pack()
        self.photo_type = ImageTk.PhotoImage
        font_path = Path("C:/Windows/Fonts/segoeui.ttf")
        self.fonts = {
            size: ImageFont.truetype(str(font_path), size)
            if font_path.exists()
            else ImageFont.load_default(size=size)
            for size in (14, 17, 22, 26)
        }
        self.pump(force=True)

    def close(self):
        if not self.closed:
            self.closed = True
            self.root.destroy()

    def toggle_speed(self, event=None):
        """Match PyBoy's Space toggle: normal speed versus unlimited speed."""
        self.fast = not self.fast
        self.apply_speed()
        self.last_draw = 0
        return "break"

    def apply_speed(self):
        # PyBoy restores its saved speed when unpausing. Reapply our preference
        # after that transition, including Space presses during model inference.
        if not self.session.paused:
            self.session.set_emulation_speed(0 if self.fast else 1)

    def frame(self):
        image = Image.new("RGB", (920, 540), "#10171d")
        draw = ImageDraw.Draw(image)
        draw.text((24, 14), "DEEPRED", font=self.fonts[26], fill="#a7ee9b")
        draw.text(
            (188, 23), f"AI plays Pokemon Red  /  {self.model}", font=self.fonts[14], fill="#afbdc5"
        )
        game = self.session.screen.image.convert("RGB").resize((480, 432), Image.Resampling.NEAREST)
        image.paste(game, (24, 66))
        draw.rounded_rectangle((526, 66, 896, 498), radius=14, fill="#1b2830")

        def lines(text, x, y, size=17, width=34, limit=2, color="#eef5f7"):
            chunks = wrap(" ".join(text.split()), width=width) or [""]
            if len(chunks) > limit:
                chunks = chunks[:limit]
                chunks[-1] = chunks[-1][: width - 3] + "..."
            for line in chunks:
                draw.text((x, y), line, font=self.fonts[size], fill=color)
                y += size + 5

        draw.text((546, 82), "GOAL", font=self.fonts[14], fill="#a7ee9b")
        lines(self.goal, 546, 105, limit=3)
        draw.text((546, 178), self.actor, font=self.fonts[14], fill="#a7ee9b")
        lines(self.current, 546, 203, size=22, width=27, limit=2)
        draw.text((546, 266), "RECENT CHOICES", font=self.fonts[14], fill="#afbdc5")
        for i, (number, label) in enumerate(self.history):
            lines(
                f"{number:02d}  {label}" if number is not None else label,
                546,
                293 + 31 * i,
                size=14,
                width=44,
                limit=1,
                color="#eef5f7" if i == len(self.history) - 1 else "#9cabb4",
            )
        lines(
            "Destination: " + self.destination
            if self.destination
            else "RAM observations · No screenshots sent to AI",
            24,
            511,
            size=14,
            width=68,
            limit=1,
            color="#afbdc5",
        )
        lines(
            f"{self.calls}/{self.limit} calls  |  {self.status}",
            546,
            468,
            size=14,
            width=44,
            limit=1,
            color="#a7ee9b",
        )
        draw.text(
            (546, 511),
            f"SPACE  ·  {'Fast (unlimited)' if self.fast else 'Normal (1x)'}",
            font=self.fonts[14],
            fill="#afbdc5",
        )
        return image

    def pump(self, force=False):
        if self.closed:
            raise EmulatorClosed()
        if force or time.monotonic() - self.last_draw >= 0.04:
            self.photo = self.photo_type(self.frame())
            self.label.configure(image=self.photo)
            self.root.update()
            self.last_draw = time.monotonic()
        if self.closed:
            raise EmulatorClosed()

    def choose(self, action, observation, number):
        self.actor = "AI CHOSE"
        self.calls = number
        self.current = decision_text(action, observation)
        self.history.append((number, self.current))
        self.status = "Acting"
        if action.get("action") == "navigate":
            self.destination = action["destination"].replace("_", " ").title()
        self.pump(force=True)

    def automatic(self, action, observation, number):
        self.actor = "AUTO POLICY"
        self.calls = number
        self.current = "Auto: attempt escape"
        self.history.append((None, self.current))
        self.status = "Acting automatically"
        self.pump(force=True)

    def hold(self, stats):
        self.calls = stats["llm_calls"]
        self.status = stats["stop_reason"].replace("_", " ").capitalize()
        self.pump(force=True)
        while not self.closed:
            self.pump()
            time.sleep(0.02)


class PresentedSession:
    """Pump the decision window during movement, dialogue, and paused inference."""

    def __init__(self, session, window):
        self.session, self.window = session, window

    def __getattr__(self, name):
        return getattr(self.session, name)

    def tick(self, count=1, *args, **kwargs):
        for _ in range(count):
            self.session.tick(1, *args, **kwargs)
            self.window.apply_speed()
            self.window.pump()
        return True
