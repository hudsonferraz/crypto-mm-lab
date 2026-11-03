"""SC-007: no private key, mnemonic, or well-known dev key is committed anywhere (R13)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THIS_FILE = Path(__file__).resolve()

# Key-like names assigned a 32-byte hex value, or a non-empty env assignment.
_ASSIGNED_HEX_KEY = re.compile(
    r"(?i)(private[_-]?key|secret[_-]?key|signer[_-]?key|deployer[_-]?key)\s*[:=]\s*['\"]?(0x)?[0-9a-f]{64}\b"
)
_ENV_KEY = re.compile(r"(?im)^\s*(PRIVATE_KEY|MNEMONIC|DEPLOYER_KEY)\s*=\s*\S+")
_CLI_KEY = re.compile(r"--private-key\s+(0x)?[0-9a-fA-F]{64}")
# Anvil's first default account key, split so this file never contains it whole.
_ANVIL_KEY = "ac0974bec39a17e36ba4a6b4d238ff94" + "4bacb478cbed5efcae784d7bf4f2ff80"
_SKIP_PREFIXES = ("contracts/lib/",)
_TEXT_SUFFIXES = {
    ".py",
    ".sol",
    ".md",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".txt",
    ".sh",
    ".html",
    ".js",
    ".example",
    ".cfg",
    ".ini",
    ".env",
    "",
}


def _tracked_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        names = [line for line in out.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        files = (p for p in ROOT.rglob("*") if p.is_file())
        names = [str(p.relative_to(ROOT)).replace("\\", "/") for p in files]
    files = []
    for name in names:
        if name.startswith(_SKIP_PREFIXES):
            continue
        path = ROOT / name
        if path.resolve() == THIS_FILE or not path.is_file():
            continue
        if path.suffix in _TEXT_SUFFIXES or path.name.startswith(".env"):
            files.append(path)
    return files


def find_key_material(text: str) -> list[str]:
    hits = []
    for pattern in (_ASSIGNED_HEX_KEY, _ENV_KEY, _CLI_KEY):
        hits.extend(match.group(0)[:40] for match in pattern.finditer(text))
    if _ANVIL_KEY in text.lower():
        hits.append("anvil default private key")
    return hits


def test_repository_contains_no_private_keys() -> None:
    offenders = {}
    for path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits = find_key_material(text)
        if hits:
            offenders[str(path.relative_to(ROOT))] = hits
    assert offenders == {}


def test_detector_catches_the_usual_shapes() -> None:
    key = "11" * 32
    assert find_key_material(f"PRIVATE_KEY={key}")
    assert find_key_material(f'private_key: "0x{key}"')
    assert find_key_material(f"forge script --private-key 0x{key}")
    assert find_key_material("MNEMONIC=test test test")
    assert find_key_material("0x" + _ANVIL_KEY)
    # Hashes and addresses are not keys.
    assert not find_key_material(f"block_hash = 0x{key}")
    assert not find_key_material("owner = 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266")
