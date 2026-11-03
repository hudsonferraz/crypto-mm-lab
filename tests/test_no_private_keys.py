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


_SIGNING_APIS = re.compile(
    r"eth_sendRawTransaction|eth_sendTransaction|send_raw_transaction|sign_transaction"
    r"|eth_account|Account\.from_key|eth_sign\b"
)
_READ_ONLY_MODULES = (
    "app/services/mempool",
    "app/adapters/dex/json_rpc.py",
    "app/adapters/dex/pending_sources.py",
    "app/adapters/dex/pool_events.py",
    "app/api/mempool_routes.py",
    "scripts/record_mempool_fixture.py",
)


def test_mempool_watcher_never_signs_or_sends() -> None:
    """Spec 004 FR-020 / D7: the watcher only reads; no signing or sending API appears."""
    offenders = []
    for name in _READ_ONLY_MODULES:
        path = ROOT / name
        files = sorted(path.rglob("*.py")) if path.is_dir() else [path]
        for file in files:
            if _SIGNING_APIS.search(file.read_text(encoding="utf-8")):
                offenders.append(str(file.relative_to(ROOT)))
    assert offenders == []
    assert _SIGNING_APIS.search("w3.eth.send_raw_transaction(tx)")


_TESTNET_MODULES = (
    "app/adapters/cex/binance_testnet.py",
    "app/services/testnet",
    "app/api/testnet_routes.py",
    "scripts/record_testnet_session.py",
)
_URL_HOST = re.compile(r"(?:https?|wss?)://([A-Za-z0-9.-]+)")


def test_testnet_trader_only_knows_the_testnet_host() -> None:
    """Spec 005 FR-001 / FR-002 / SC-007: no code path names any exchange host but the testnet."""
    hosts: dict[str, set[str]] = {}
    for name in _TESTNET_MODULES:
        path = ROOT / name
        files = sorted(path.rglob("*.py")) if path.is_dir() else [path]
        for file in files:
            text = file.read_text(encoding="utf-8")
            found = set(_URL_HOST.findall(text))
            if "api.binance.com" in text or "binance.us" in text:
                found.add("mainnet")
            if found - {"testnet.binance.vision"}:
                hosts[str(file.relative_to(ROOT))] = found
    assert hosts == {}


def test_no_mainnet_exchange_key_setting_exists() -> None:
    """Only testnet keys can be configured; a mainnet key variable is never read (D1)."""
    from app.config.settings import Settings

    key_fields = {n for n in Settings.model_fields if "key" in n or "secret" in n}
    assert key_fields == {"binance_testnet_api_key", "binance_testnet_api_secret"}
