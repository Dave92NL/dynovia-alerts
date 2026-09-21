"""Build the PWA icons from the club crest. Run by hand when the crest changes.

    py -3.12 scripts/ikony.py

Pillow is not a project dependency: this produces committed files and never
runs in Actions. Install it ad hoc if you need to regenerate them.

The crest PNG has transparency where the design is *white* - the band behind
"DYNOVIA", the stripes, the "DYNÓW" band - not just around the outside. It is
therefore composited onto white here rather than used as-is, or a home screen
icon would show the wallpaper through the middle of the shield.
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
CREST = ROOT / "ikona herb.png"
ICONS = ROOT / "web" / "icons"

WHITE = (255, 255, 255, 255)
SIZES = {"icon-192.png": 192, "icon-512.png": 512, "apple-touch-icon.png": 180}
MASKABLE = 512
MASKABLE_SAFE = 0.68
"""Android crops a maskable icon to whatever shape the launcher likes, so the
crest only gets the middle two thirds."""


def crest_on_white(size: int, fill: float) -> Image.Image:
    crest = Image.open(CREST).convert("RGBA")
    crest = crest.crop(crest.getchannel("A").getbbox())  # drop the empty margin

    side = int(size * fill)
    crest.thumbnail((side, side), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), WHITE)
    canvas.alpha_composite(
        crest, ((size - crest.width) // 2, (size - crest.height) // 2)
    )
    return canvas.convert("RGB")


def main() -> None:
    ICONS.mkdir(parents=True, exist_ok=True)
    for name, size in SIZES.items():
        crest_on_white(size, 0.88).save(ICONS / name, optimize=True)
        print(f"{name}  {size}x{size}")
    crest_on_white(MASKABLE, MASKABLE_SAFE).save(
        ICONS / "icon-512-maskable.png", optimize=True
    )
    print(f"icon-512-maskable.png  {MASKABLE}x{MASKABLE}")


if __name__ == "__main__":
    main()
