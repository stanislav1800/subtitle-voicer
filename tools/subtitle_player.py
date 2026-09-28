"""Test window that plays subtitles over a moving background (for ``cli watch`` checks).

Run: uv run python tools/subtitle_player.py [--loop]

Then, in another terminal:
    uv run python -m subvoicer.cli watch --exe python.exe --title "Тестовые субтитры" \
        --rect 0.05,0.74,0.95,0.98 --preset bright_on_dark --speaker prefix --debug

The scenario covers the stage 2 criteria: a line shown for a while (printed once), the same
line twice through an empty zone (printed twice), typewriter text (one line), a long
typewriter line (a continuation), "Совет: …" (no speaker), a line replaced without a gap.
Each shown line is echoed here with a timestamp to compare with the watch output.
"""

from __future__ import annotations

import argparse
import random
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime

WIDTH, HEIGHT = 1280, 720
FONT = ("Arial", 30, "bold")
SUB_BOTTOM = int(HEIGHT * 0.96)  # subtitles grow upwards: long lines wrap to two rows
WRAP = WIDTH - 160
TYPED_TOP = int(HEIGHT * 0.79)  # room for two rows above SUB_BOTTOM
FPS = 30


@dataclass
class Step:
    kind: str  # "show" | "type" | "gap"
    text: str = ""
    seconds: float = 0.0
    char_s: float = 0.04  # typewriter speed


SCENARIO = [
    Step("show", "Джуди: Ты вовремя. Заходи, покажу кое-что.", 3),
    Step("gap", seconds=1),
    Step("show", "Да.", 2),
    Step("gap", seconds=1),
    Step("show", "Да.", 2),
    Step("gap", seconds=1),
    Step("type", "Слушай внимательно, повторять не буду.", 2),
    Step("gap", seconds=1),
    Step("show", "Совет: нажмите Q, чтобы уклониться.", 3),
    Step("gap", seconds=1),
    Step("show", "Мост перекрыт, придётся ехать в объезд.", 2.5),
    Step("show", "Панам: Я знаю короткую дорогу через доки.", 3),
    Step("gap", seconds=1),
    Step(
        "type",
        "Охрана на входе сменится через двадцать минут, у нас будет окно, так что не опаздывай.",
        3,
        char_s=0.06,
    ),
    Step("gap", seconds=2),
]


class Player:
    def __init__(self, root: tk.Tk, loop: bool) -> None:
        self.root = root
        self.loop = loop
        self.canvas = tk.Canvas(
            root, width=WIDTH, height=HEIGHT, bg="#101418", highlightthickness=0
        )
        self.canvas.pack()
        rng = random.Random(7)
        self.blobs = []
        for _ in range(40):  # a slowly panning dark "scene" so the window sends frames
            x, y = rng.randint(0, WIDTH), rng.randint(0, HEIGHT)
            r = rng.randint(20, 120)
            red, green, blue = (rng.randint(20, 90) for _ in range(3))
            color = f"#{red:02x}{green:02x}{blue:02x}"
            blob = self.canvas.create_oval(x - r, y - r, x + r, y + r, fill=color, outline="")
            self.blobs.append((blob, rng.uniform(-3, 3)))
        self.text_items: list[int] = []
        self.index = 0
        self.animate()
        self.next_step()

    def animate(self) -> None:
        for blob, dx in self.blobs:
            self.canvas.move(blob, dx, 0)
            x1, _, x2, _ = self.canvas.coords(blob)
            if x1 > WIDTH:
                self.canvas.move(blob, -WIDTH - (x2 - x1), 0)
            elif x2 < 0:
                self.canvas.move(blob, WIDTH + (x2 - x1), 0)
        for item in self.text_items:
            self.canvas.tag_raise(item)
        self.root.after(1000 // FPS, self.animate)

    def draw(self, text: str, typed: bool = False) -> None:
        """Shown lines are centred and grow upwards; typed text is left-aligned from a fixed
        top-left corner, so characters appear in their final places as in games."""
        for item in self.text_items:
            self.canvas.delete(item)
        self.text_items = []
        if not text:
            return
        for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2), (-2, 2), (2, -2)]:
            self.text_items.append(self.put_text(text, dx, dy, "black", typed))  # outline
        self.text_items.append(self.put_text(text, 0, 0, "white", typed))

    def put_text(self, text: str, dx: int, dy: int, fill: str, typed: bool) -> int:
        if typed:
            return self.canvas.create_text(
                (WIDTH - WRAP) // 2 + dx,
                TYPED_TOP + dy,
                text=text,
                font=FONT,
                fill=fill,
                anchor="nw",
                width=WRAP,
                justify="left",
            )
        return self.canvas.create_text(
            WIDTH // 2 + dx,
            SUB_BOTTOM + dy,
            text=text,
            font=FONT,
            fill=fill,
            anchor="s",
            width=WRAP,
            justify="center",
        )

    def echo(self, text: str) -> None:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {text}", flush=True)

    def next_step(self) -> None:
        if self.index >= len(SCENARIO):
            if not self.loop:
                self.root.after(500, self.root.destroy)
                return
            self.index = 0
        step = SCENARIO[self.index]
        self.index += 1
        if step.kind == "gap":
            self.draw("")
            self.root.after(int(step.seconds * 1000), self.next_step)
        elif step.kind == "show":
            self.draw(step.text)
            self.echo(step.text)
            self.root.after(int(step.seconds * 1000), self.next_step)
        else:
            self.echo(f"(печатается) {step.text}")
            self.type_chars(step, 1)

    def type_chars(self, step: Step, count: int) -> None:
        self.draw(step.text[:count], typed=True)
        if count < len(step.text):
            self.root.after(int(step.char_s * 1000), self.type_chars, step, count + 1)
        else:
            self.root.after(int(step.seconds * 1000), self.next_step)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--loop", action="store_true", help="повторять сценарий по кругу")
    args = parser.parse_args()
    root = tk.Tk()
    root.title("Тестовые субтитры")
    root.resizable(False, False)
    Player(root, args.loop)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
