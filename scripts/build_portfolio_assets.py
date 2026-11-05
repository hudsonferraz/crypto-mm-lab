"""Capture the README images and the demo GIF from a running app.

Drives headless Chrome over the DevTools protocol (no Selenium or Playwright),
in the dark theme at 1280 px wide:

- dashboard.png: the simulation lab with a result (first screen)
- dashboard-lab.png: the full simulation lab page, three runs compared
- dashboard-killswitch.png: live market making with the kill switch on
- dashboard-lp.png, dashboard-arb.png, dashboard-mempool.png, dashboard-testnet.png
- dashboard-mempool-replay.png: only with --mempool-replay, against an app started
  with MEMPOOL_MODE=replay
- dashboard-docker.png: only with --docker, against the Docker Compose stack
- demo.gif: nine frames (overview, lab, comparison, then each section), 1080 x 675

Start the app first (`uvicorn app.main:app`); give it a minute so live numbers
fill in. Needs Chrome or Edge, `websockets` (a dependency) and Pillow:

    python scripts/build_portfolio_assets.py --base http://localhost:8000
    python scripts/build_portfolio_assets.py --base http://localhost:8001 --mempool-replay
    python scripts/build_portfolio_assets.py --base http://localhost:8000 --docker

The kill switch is turned on for one screenshot and off again; on a server with
OPERATOR_API_TOKEN set, pass it with --operator-token.
"""

import argparse
import asyncio
import base64
import io
import json
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import httpx
import websockets
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "docs" / "images"
CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "chromium",
    "chromium-browser",
]
SECTION_HEIGHT = 2300  # long sections are cropped so the README stays readable
LAB_RUNS = ((8, "trade_fill"), (3, "full_cross_fill"), (3, "trade_fill"))


def find_chrome(explicit: str | None) -> str:
    for candidate in [explicit, *CHROME_PATHS]:
        if candidate and (Path(candidate).exists() or shutil.which(candidate)):
            return candidate
    raise SystemExit("Chrome or Edge not found; pass --chrome")


class Page:
    def __init__(self, ws) -> None:
        self.ws = ws
        self.n = 0

    async def call(self, method: str, **params):
        self.n += 1
        my_id = self.n
        await self.ws.send(json.dumps({"id": my_id, "method": method, "params": params}))
        while True:
            message = json.loads(await self.ws.recv())
            if message.get("id") == my_id:
                if "error" in message:
                    raise RuntimeError(message["error"])
                return message.get("result", {})

    async def js(self, expression: str):
        result = await self.call(
            "Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True
        )
        if result.get("exceptionDetails"):
            raise RuntimeError(result["exceptionDetails"])
        return result.get("result", {}).get("value")

    async def wait_for(self, expression: str, timeout: float = 60) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self.js(expression):
                return
            await asyncio.sleep(0.5)
        raise TimeoutError(expression)

    async def capture(self, full: bool) -> Image.Image:
        params: dict = {"format": "png", "captureBeyondViewport": full}
        if full:
            content = (await self.call("Page.getLayoutMetrics"))["cssContentSize"]
            params["clip"] = {
                "x": 0,
                "y": 0,
                "width": content["width"],
                "height": min(content["height"], 6000),
                "scale": 1,
            }
        data = (await self.call("Page.captureScreenshot", **params))["data"]
        return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")

    async def show(self, section: str, settle: float = 5) -> None:
        await self.js(f"location.hash = '#{section}'; window.scrollTo(0, 0); true")
        await asyncio.sleep(settle)


def save(image: Image.Image, name: str, crop: int | None = None) -> None:
    if crop and image.height > crop:
        image = image.crop((0, 0, image.width, crop))
    image.save(IMAGES / name, optimize=True)
    print(f"saved docs/images/{name} {image.size[0]}x{image.size[1]}")


async def run_lab(page: Page) -> None:
    await page.show("lab", settle=2)
    await page.wait_for("document.getElementById('lab-bundle').options.length > 0")
    for spread, model in LAB_RUNS:
        await page.js(
            f"document.getElementById('lab-spread').value = {spread};"
            f"document.getElementById('lab-fill').value = '{model}';"
            "document.getElementById('lab-fill').dispatchEvent(new Event('change'));"
            "document.getElementById('lab-run').click(); true"
        )
        await page.wait_for("document.querySelector('#lab-result .summary-list') !== null")
        await asyncio.sleep(1.5)


async def compare_runs(page: Page) -> None:
    await page.js("refreshRunList(); true")
    await asyncio.sleep(2)
    await page.js(
        "document.querySelectorAll('[data-compare]').forEach((box) => {"
        " if (!box.disabled && !box.checked) box.click(); }); true"
    )
    await asyncio.sleep(3)


async def capture_all(page: Page, args) -> None:
    base = args.base.rstrip("/")
    await page.call(
        "Emulation.setDeviceMetricsOverride",
        width=1280,
        height=800,
        deviceScaleFactor=1,
        mobile=False,
    )
    await page.call("Page.navigate", url=f"{base}/dashboard#overview")
    await asyncio.sleep(2)
    await page.js("localStorage.setItem('mmlab-theme', 'dark'); location.reload(); true")
    await asyncio.sleep(8)

    if args.docker:
        await page.show("live")
        save(await page.capture(full=True), "dashboard-docker.png", crop=SECTION_HEIGHT)
        return
    if args.mempool_replay:
        await page.show("mempool", settle=8)
        save(await page.capture(full=True), "dashboard-mempool-replay.png", crop=SECTION_HEIGHT)
        return

    frames = [await page.capture(full=False)]  # overview
    await run_lab(page)
    lab_first_screen = await page.capture(full=False)
    save(lab_first_screen, "dashboard.png")
    frames.append(lab_first_screen)
    await page.js(
        "document.getElementById('lab-pnl-chart').scrollIntoView({block: 'center'}); true"
    )
    await asyncio.sleep(1)
    frames.append(await page.capture(full=False))
    await compare_runs(page)
    await page.js(
        "document.getElementById('lab-compare').scrollIntoView({block: 'start'});"
        " window.scrollBy(0, -80); true"
    )
    await asyncio.sleep(1)
    frames.append(await page.capture(full=False))
    await page.js("window.scrollTo(0, 0); true")
    save(await page.capture(full=True), "dashboard-lab.png")

    for section in ("live", "lp", "arb", "mempool", "testnet"):
        await page.show(section)
        frames.append(await page.capture(full=False))
        if section != "live":
            save(await page.capture(full=True), f"dashboard-{section}.png", crop=SECTION_HEIGHT)

    headers = {"X-Operator-Token": args.operator_token} if args.operator_token else {}
    with httpx.Client(base_url=base, timeout=30, headers=headers) as client:
        client.post("/kill-switch", json={"active": True}).raise_for_status()
        try:
            await page.show("live", settle=6)
            save(await page.capture(full=True), "dashboard-killswitch.png")
        finally:
            client.post("/kill-switch", json={"active": False}).raise_for_status()

    small = [frame.resize((1080, 675), Image.LANCZOS) for frame in frames]
    palette = [frame.quantize(colors=256, method=Image.Quantize.MEDIANCUT) for frame in small]
    palette[0].save(
        IMAGES / "demo.gif", save_all=True, append_images=palette[1:], duration=2500, loop=0
    )
    print(f"saved docs/images/demo.gif ({len(frames)} frames)")


async def main(args) -> None:
    chrome = find_chrome(args.chrome)
    profile = tempfile.mkdtemp()
    port = 9340
    browser = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--hide-scrollbars",
            "about:blank",
        ]
    )
    try:
        tabs = None
        for _ in range(100):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json"))
                break
            except OSError:
                time.sleep(0.2)
        if tabs is None:
            raise SystemExit("Chrome did not start")
        target = next(tab for tab in tabs if tab["type"] == "page")
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=50_000_000) as ws:
            page = Page(ws)
            await page.call("Page.enable")
            await capture_all(page, args)
    finally:
        browser.terminate()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--chrome")
    parser.add_argument("--operator-token")
    parser.add_argument("--mempool-replay", action="store_true")
    parser.add_argument("--docker", action="store_true")
    IMAGES.mkdir(parents=True, exist_ok=True)
    asyncio.run(main(parser.parse_args()))
