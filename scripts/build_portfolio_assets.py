"""Build demo.gif and copy dashboard screenshots into docs/images."""

import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "docs" / "images"
SCREENSHOTS = Path.home() / ".cursor" / "screenshots"
if sys.platform == "win32":
    alt = Path(r"C:\Users\T-GAMER\AppData\Local\Temp\cursor\screenshots")
    if alt.exists():
        SCREENSHOTS = alt


def copy_dashboard_screenshots() -> None:
    IMAGES.mkdir(parents=True, exist_ok=True)
    mapping = {
        "dashboard-mm-lab.png": "dashboard.png",
        "dashboard-killswitch.png": "dashboard-killswitch.png",
        "dashboard-docker.png": "dashboard-docker.png",
        "grafana-dashboard.png": "grafana.png",
    }
    for source_name, dest_name in mapping.items():
        source = SCREENSHOTS / source_name
        if source.exists():
            shutil.copy(source, IMAGES / dest_name)


def build_demo_gif() -> None:
    frame_names = [
        "dashboard-mm-lab.png",
        "frame-02.png",
        "frame-03.png",
        "frame-04.png",
        "frame-05.png",
    ]
    frames: list[Image.Image] = []
    for name in frame_names:
        path = SCREENSHOTS / name
        if not path.exists():
            continue
        frames.append(Image.open(path).convert("RGB"))

    if not frames:
        return

    width, height = frames[0].size
    normalized = [frame.resize((width, height)) for frame in frames]
    normalized[0].save(
        IMAGES / "demo.gif",
        save_all=True,
        append_images=normalized[1:],
        duration=2000,
        loop=0,
    )


def render_backtest_screenshot() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_backtest.py"),
            "--fixture",
            str(ROOT / "tests" / "fixtures" / "orderbook_snapshots.csv"),
            "--strategy",
            "pure_mm",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    lines = result.stdout.strip().splitlines()
    line_height = 28
    padding = 24
    width = 720
    height = padding * 2 + line_height * len(lines)
    image = Image.new("RGB", (width, height), color=(15, 17, 21))
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("consola.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    y = padding
    for line in lines:
        draw.text((padding, y), line, fill=(232, 234, 237), font=font)
        y += line_height
    image.save(IMAGES / "backtest.png")


def render_architecture_screenshot() -> None:
    width, height = 900, 520
    image = Image.new("RGB", (width, height), color=(15, 17, 21))
    draw = ImageDraw.Draw(image)
    try:
        title_font = ImageFont.truetype("arial.ttf", 22)
        body_font = ImageFont.truetype("consola.ttf", 16)
    except OSError:
        title_font = ImageFont.load_default()
        body_font = ImageFont.load_default()

    draw.text((24, 20), "crypto-mm-lab architecture", fill=(232, 234, 237), font=title_font)
    lines = [
        "CCXT adapter  -->  normalizer  -->  order book",
        "Web3 adapter  -->  AMM pool",
        "                         |",
        "                 market maker loop",
        "                         |",
        "         strategy --> risk --> paper broker",
        "                         |",
        "              analytics / SQLite / Postgres",
        "                         |",
        "           FastAPI + Prometheus + Grafana",
        "",
        "Backtest: replay snapshots -> same pipeline (no network)",
    ]
    y = 70
    for line in lines:
        draw.text((24, y), line, fill=(154, 160, 166), font=body_font)
        y += 30
    image.save(IMAGES / "architecture.png")


def main() -> None:
    copy_dashboard_screenshots()
    build_demo_gif()
    render_backtest_screenshot()
    render_architecture_screenshot()
    print(f"Assets written to {IMAGES}")


if __name__ == "__main__":
    main()
