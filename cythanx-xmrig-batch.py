#!/usr/bin/env python3
"""
CYTHANX INTENSIVE XMRIG AUTOMATION

Fuses the five uploaded top-level sources through a random-key,
Enigma-inspired transform, derives a CYTHANX fusion commitment, then uses
that commitment as auxiliary organism/telemetry state around native XMRig.

XMRig remains the actual miner. The CYTHANX transform is NOT a native
RandomX/XMRig proof-of-work implementation.

No wallet, pool password, or remote API token is embedded. Supply:
  --user / XMRIG_USER
  --password / XMRIG_PASS

Examples:
  python cythanx_mega_xmrig.py --self-test
  python cythanx_mega_xmrig.py --fuse
  python cythanx_mega_xmrig.py --mine --user 'XNO:nano_...worker'
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    from blake3 import blake3 as _blake3
    BLAKE3_BACKEND = "blake3"
except ImportError:
    _blake3 = None
    BLAKE3_BACKEND = "blake2b-fallback"


SOURCE_MANIFEST = [
    {
        "name": "1517.png",
        "sha256": "5caef3c22b9da0b1077a118d2f3b7795629426ae5b46c92047adb1ae47a898cd",
        "bytes": 810155
    },
    {
        "name": "1401.png",
        "sha256": "d3b1360a298a66bb447fa0fb46697e778220eb9cfff8d770b5004070c547aa82",
        "bytes": 642026
    },
    {
        "name": "cythanx_dispatcher.py",
        "sha256": "d2e9a85d60fff2e60c3122e4acfbea53a3b0a9090a1548309e87868c0ef3c774",
        "bytes": 3372
    },
    {
        "name": "altMiner-main (2).zip",
        "sha256": "fd7e617fba2a904be1d6a73694619cd92846c4b6e1e7afae87b0560a2fc4d5ee",
        "bytes": 6065609
    },
    {
        "name": "cyx-xmrig.py",
        "sha256": "9237d825a9e95e13a2f32348d81ca0577c29dd743c37385052cf06c2c7dd6043",
        "bytes": 13903
    }
]
SOURCE_NAMES = [item["name"] for item in SOURCE_MANIFEST]


# ---------------------------------------------------------------------------
# HASH / ENIGMA FUSION
# ---------------------------------------------------------------------------

def blake3_256(data: bytes) -> bytes:
    if _blake3 is not None:
        return _blake3(data).digest(length=32)
    # Explicit non-BLAKE3 fallback for environments without the optional
    # dependency. Install blake3 for exact source-compatible behavior.
    return hashlib.blake2b(
        b"CYTHANX|BLAKE3-FALLBACK|" + data,
        digest_size=32,
    ).digest()


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def double_sha256(data: bytes) -> bytes:
    return sha256(sha256(data))


def scrypt256(data: bytes, salt: bytes | None = None) -> bytes:
    return hashlib.scrypt(
        password=data,
        salt=salt if salt is not None else data,
        n=1024,
        r=1,
        p=1,
        dklen=32,
    )


def _rotl8(x: int, n: int) -> int:
    n &= 7
    return ((x << n) | (x >> (8 - n))) & 0xFF


def enigma_bytes(data: bytes, key: bytes) -> bytes:
    """Random-key rotor-inspired transform; not claimed as secure encryption."""
    if len(key) < 32:
        raise ValueError("Enigma key must be at least 32 bytes")
    out = bytearray(len(data))
    for block_start in range(0, len(data), 32):
        block = data[block_start:block_start + 32]
        counter = block_start // 32
        stream = sha256(
            b"CYTHANX|ENIGMA|STREAM|" + key + counter.to_bytes(8, "big")
        )
        for j, byte in enumerate(block):
            k = stream[j]
            x = (byte + k + ((block_start + j) * 17)) & 0xFF
            x ^= (key[(counter + j) % len(key)] + j * 29) & 0xFF
            x = _rotl8(x, (k ^ key[j % len(key)]) & 7)
            out[block_start + j] = x
    return bytes(out)


def resolve_source(name: str, root: Path) -> Path | None:
    for candidate in (root / name, Path("/mnt/data") / name, Path(name)):
        if candidate.exists():
            return candidate
    return None


def zip_member_digest(path: Path) -> list[dict]:
    if path.suffix.lower() != ".zip":
        return []
    result = []
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            data = zf.read(info.filename)
            result.append({
                "member": info.filename,
                "bytes": len(data),
                "sha256": sha256(data).hex(),
            })
    return result


def fuse_sources(root: Path, enigma_seed: str | None = None) -> dict:
    if enigma_seed:
        try:
            key = bytes.fromhex(enigma_seed)
        except ValueError:
            key = sha256(enigma_seed.encode())
    else:
        key = secrets.token_bytes(32)

    if len(key) < 32:
        key = sha256(key)

    records = []
    fused_chunks = []

    for item in SOURCE_MANIFEST:
        path = resolve_source(item["name"], root)
        if path is None:
            records.append({
                "name": item["name"],
                "status": "missing",
                "expected_sha256": item["sha256"],
            })
            continue

        raw = path.read_bytes()
        transformed = enigma_bytes(raw, key)
        actual = sha256(raw).hex()

        records.append({
            "name": item["name"],
            "status": "ok",
            "bytes": len(raw),
            "expected_sha256": item["sha256"],
            "actual_sha256": actual,
            "matches_manifest": actual == item["sha256"],
            "enigma_sha256": sha256(transformed).hex(),
        })

        if path.suffix.lower() == ".zip":
            records[-1]["zip_members"] = zip_member_digest(path)

        fused_chunks.append(
            item["name"].encode() + b"\0" +
            len(raw).to_bytes(8, "big") +
            bytes.fromhex(actual) +
            transformed
        )

    envelope = b"CYTHANX|MEGA-FUSION|V1|" + b"".join(fused_chunks)
    fusion_root = blake3_256(envelope)
    fusion_state = double_sha256(b"CYTHANX|FUSION-SHA256D|" + fusion_root)
    fusion_commitment = blake3_256(
        b"CYTHANX|FUSION-COMMIT|" + fusion_root + fusion_state + key
    )

    return {
        "version": 1,
        "source_count": len(SOURCE_MANIFEST),
        "key_hex": key.hex(),
        "records": records,
        "fusion_root": fusion_root.hex(),
        "fusion_state": fusion_state.hex(),
        "fusion_commitment": fusion_commitment.hex(),
    }


# ---------------------------------------------------------------------------
# CYTHANX ORGANISM CORE
# ---------------------------------------------------------------------------

def organism_joatt(data: bytes, previous: bytes = b"") -> dict:
    root = blake3_256(data + previous)
    tree = double_sha256(root + data)
    stretched = hashlib.pbkdf2_hmac(
        "sha512", tree, b"CYTHANX|JOATT|2048", 2048, 64
    )
    memory = scrypt256(stretched[:32], b"CYTHANX|MEMORY")
    mix = blake3_256(root + tree + memory)
    return {
        "root": root.hex(),
        "sha256d": tree.hex(),
        "memory": memory.hex(),
        "state256": mix.hex(),
        "identifier64": mix[:8].hex(),
    }


def organism_cythanize(data: bytes, domain: bytes = b"VECTOR") -> dict:
    vector = blake3_256(b"CYTHANX|" + domain + b"|" + data)
    digest = double_sha256(vector)
    work = scrypt256(digest, b"CYTHANX|VECTOR|SCRYPT")
    commitment = blake3_256(vector + digest + work)
    return {
        "vector": vector.hex(),
        "sha256d": digest.hex(),
        "scrypt": work.hex(),
        "commitment": commitment.hex(),
    }


def organism_transmogrify(data: bytes, rounds: int = 3) -> bytes:
    x = data
    for i in range(rounds):
        local_key = sha256(b"CYTHANX|LOCAL-ROTOR|" + i.to_bytes(4, "big"))
        x = enigma_bytes(x, local_key)
        x = blake3_256(b"CYTHANX|TRANSMOGRIFY|" + bytes([i & 0xFF]) + x)
        x = scrypt256(x, f"round-{i}".encode())
    return x


@dataclass
class OrganismState:
    energy: float = 1.0
    entropy: float = 0.0
    health: float = 1.0
    generation: int = 0

    def adapt(self, signal_value: bytes) -> None:
        x = int.from_bytes(blake3_256(signal_value)[:8], "big") / 2**64
        self.entropy = -math.log2(max(x, 2**-64)) / 64.0
        learning = 0.02 + 0.08 * x
        self.energy = max(0.0, min(2.0, self.energy * (0.98 + learning)))
        self.health = max(
            0.0,
            min(1.0, 0.97 * self.health + 0.03 * (1.0 - abs(x - 0.5))),
        )
        self.generation += 1


def run_organism(seed: bytes, rounds: int = 3) -> dict:
    if not 1 <= rounds <= 1000:
        raise ValueError("rounds must be between 1 and 1000")

    header = (
        b"CYTHANX-HEADER" +
        (1).to_bytes(4, "big") +
        blake3_256(seed) +
        double_sha256(seed) +
        int(time.time()).to_bytes(4, "big") +
        b"\0" * 8
    )[:80].ljust(80, b"\0")

    state = blake3_256(b"CYTHANX|GENESIS|" + seed)
    organism = OrganismState()
    previous = b""

    for i in range(rounds):
        jo = organism_joatt(
            enigma_bytes(header + state, sha256(seed + i.to_bytes(4, "big"))),
            previous,
        )
        vector = organism_cythanize(bytes.fromhex(jo["state256"]), b"ORGANISM")
        transformed = organism_transmogrify(
            bytes.fromhex(vector["commitment"]), 3 + (i % 3)
        )
        aux128 = blake3_256(b"CYTHANX|AUX128|" + transformed)[:16]
        state = blake3_256(
            b"CYTHANX|DYNO256|" + state + aux128 + i.to_bytes(8, "big")
        )
        organism.adapt(aux128)
        final = blake3_256(
            b"CYTHANX|IMPOSSIBLE|" + header + state +
            bytes.fromhex(vector["commitment"]) +
            organism.generation.to_bytes(8, "big")
        )
        previous = bytes.fromhex(jo["state256"])
        header = blake3_256(header + state + final)

    return {
        "network": "CYTHAN-MAINNET",
        "chain_id": "CYTHAN-04-BED-BTCADDR",
        "final_dyno_state": state.hex(),
        "organism": asdict(organism),
    }


# ---------------------------------------------------------------------------
# XMRIG SUPERVISOR
# ---------------------------------------------------------------------------

STOP = threading.Event()
PROCESS: subprocess.Popen | None = None


def detected_threads() -> int:
    return max(1, os.cpu_count() or 1)


def choose_threads(value: int | None) -> int:
    available = detected_threads()
    if value is None:
        return available
    if value <= 0:
        raise ValueError("--threads must be greater than zero")
    return min(value, available)


def api_get(port: int, path: str) -> dict | None:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
        with urllib.request.urlopen(req, timeout=2) as response:
            return json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def build_xmrig_command(args: argparse.Namespace, threads: int) -> list[str]:
    cmd = [
        args.xmrig,
        "--algo", args.algo,
        "--url", args.pool,
        "--user", args.user,
        "--pass", args.password,
        "--threads", str(threads),
        "--http-host", "127.0.0.1",
        "--http-port", str(args.api_port),
        "--print-time", str(args.print_time),
    ]

    if args.profile == "intensive":
        cmd += [
            "--randomx-mode", "fast",
            "--randomx-init", str(threads),
            "--cpu-no-yield",
            "--huge-pages",
        ]
    elif args.no_yield:
        cmd.append("--cpu-no-yield")

    if args.tls:
        cmd.append("--tls")
    if args.rig_id:
        cmd += ["--rig-id", args.rig_id]
    return cmd


def redacted_command(cmd: list[str]) -> str:
    redacted = list(cmd)
    for i, item in enumerate(redacted[:-1]):
        if item in ("--user", "--pass"):
            redacted[i + 1] = "<redacted>"
    return " ".join(redacted)


def start_xmrig(args: argparse.Namespace, threads: int) -> subprocess.Popen:
    cmd = build_xmrig_command(args, threads)
    print("[XMRIG] launching:", redacted_command(cmd), flush=True)
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )


def stop_xmrig() -> None:
    global PROCESS
    if PROCESS is None:
        return
    if PROCESS.poll() is not None:
        PROCESS = None
        return
    try:
        if os.name == "nt":
            PROCESS.terminate()
        else:
            PROCESS.send_signal(signal.SIGINT)
        PROCESS.wait(timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        try:
            PROCESS.kill()
            PROCESS.wait(timeout=5)
        except OSError:
            pass
    finally:
        PROCESS = None


def organism_monitor(fusion: dict, interval: float, rounds: int, api_port: int) -> None:
    seed = bytes.fromhex(fusion["fusion_commitment"])
    while not STOP.wait(interval):
        try:
            result = run_organism(seed, rounds)
            org = result["organism"]
            summary = api_get(api_port, "/1/summary")
            total = ((summary or {}).get("hashrate") or {}).get("total", ["-"])
            print(
                "[CYTHANX] "
                f"gen={org['generation']} "
                f"energy={org['energy']:.6f} "
                f"health={org['health']:.6f} "
                f"entropy={org['entropy']:.6f} "
                f"hashrate={total[0] if total else '-'}",
                flush=True,
            )
        except Exception as exc:
            print(f"[CYTHANX] monitor error: {exc}", file=sys.stderr)


def supervise(args: argparse.Namespace, fusion: dict) -> int:
    global PROCESS
    threads = choose_threads(args.threads)
    STOP.clear()

    monitor = threading.Thread(
        target=organism_monitor,
        args=(fusion, args.organism_interval, args.organism_rounds, args.api_port),
        daemon=True,
    )
    monitor.start()

    restarts = 0
    backoff = max(1, args.restart_delay)

    def shutdown(signum, frame):
        STOP.set()
        stop_xmrig()

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    try:
        while not STOP.is_set():
            PROCESS = start_xmrig(args, threads)

            while not STOP.is_set():
                code = PROCESS.poll()
                if code is not None:
                    print(f"[XMRIG] exited with code {code}", flush=True)
                    PROCESS = None
                    break
                time.sleep(0.5)

            if STOP.is_set():
                break

            restarts += 1
            if args.max_restarts is not None and restarts > args.max_restarts:
                print("[XMRIG] maximum restart count reached", flush=True)
                return 1

            print(f"[XMRIG] restarting in {backoff}s", flush=True)
            STOP.wait(backoff)
            backoff = min(backoff * 2, 60)

        return 0
    finally:
        STOP.set()
        stop_xmrig()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def self_test() -> dict:
    key = b"\0" * 32
    transformed = enigma_bytes(b"CYTHANX-SELF-TEST", key)
    seed = blake3_256(b"TEST|" + transformed)
    organism = run_organism(seed, 2)
    assert len(bytes.fromhex(organism["final_dyno_state"])) == 32
    assert organism["organism"]["generation"] == 2
    return {
        "ok": True,
        "blake3_backend": BLAKE3_BACKEND,
        "enigma_bytes": len(transformed),
        "final_dyno_state": organism["final_dyno_state"],
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="CYTHANX mega source-fusion + XMRig automation"
    )
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--fuse", action="store_true")
    p.add_argument("--root", default=".")
    p.add_argument("--enigma-seed", default=None)
    p.add_argument("--mine", action="store_true")
    p.add_argument("--xmrig", default=os.environ.get("XMRIG_PATH", "xmrig"))
    p.add_argument("--pool", default=os.environ.get("XMRIG_POOL", "xmrig.nanswap.com:3333"))
    p.add_argument("--user", default=os.environ.get("XMRIG_USER"))
    p.add_argument("--password", default=os.environ.get("XMRIG_PASS", "x"))
    p.add_argument("--rig-id", default=os.environ.get("XMRIG_RIG_ID", "cythanx-mega"))
    p.add_argument("--algo", default=os.environ.get("XMRIG_ALGO", "rx/0"))
    p.add_argument("--threads", type=int, default=None)
    p.add_argument(
        "--profile",
        choices=("balanced", "intensive"),
        default="intensive",
        help="XMRig CPU profile; intensive favors throughput over responsiveness",
    )
    p.add_argument(
        "--no-yield",
        action="store_true",
        help="allow XMRig CPU backend to favor maximum hashrate",
    )
    p.add_argument("--tls", action="store_true")
    p.add_argument("--api-port", type=int, default=16000)
    p.add_argument("--print-time", type=int, default=60)
    p.add_argument("--organism-interval", type=float, default=15.0)
    p.add_argument("--organism-rounds", type=int, default=3)
    p.add_argument("--restart-delay", type=int, default=5)
    p.add_argument("--max-restarts", type=int, default=None)
    return p.parse_args()



def build_batch_launcher(script_path: str) -> str:
    """Return a Windows batch supervisor for CYTHANX + XMRig."""
    script = str(Path(script_path).resolve())
    return (
        "@echo off\n"
        "setlocal EnableExtensions EnableDelayedExpansion\n"
        "title CYTHANX + XMRig Intensive Miner\n"
        "cd /d \"%~dp0\"\n"
        "if not defined CYX_WALLET (\n"
        "  echo [CYTHANX] Set CYX_WALLET=YOUR_WALLET_OR_WORKER first.\n"
        "  exit /b 2\n"
        ")\n"
        "where python >nul 2>nul\n"
        "if errorlevel 1 (\n"
        "  echo [CYTHANX] Python was not found in PATH.\n"
        "  exit /b 3\n"
        ")\n"
        ":run\n"
        "echo [CYTHANX] Starting supervised intensive job...\n"
        f'python "{script}" --mine --profile intensive --user "%CYX_WALLET%" %*\n'
        "set \"RC=%ERRORLEVEL%\"\n"
        "if \"%CYX_NO_RESTART%\"==\"1\" exit /b !RC!\n"
        "echo [CYTHANX] Exited with code !RC! - restarting in 10 seconds...\n"
        "timeout /t 10 /nobreak >nul\n"
        "goto run\n"
    )

def main() -> int:
    args = parse_args()

    if args.self_test:
        print(json.dumps(self_test(), indent=2))
        return 0

    fusion = fuse_sources(Path(args.root).resolve(), args.enigma_seed)

    if args.fuse or not args.mine:
        print(json.dumps(fusion, indent=2))

    if not args.mine:
        return 0

    if not args.user:
        print(
            "ERROR: --user (or XMRIG_USER) is required for mining. "
            "No wallet/payout identity is embedded.",
            file=sys.stderr,
        )
        return 2

    if args.threads is not None and args.threads < 1:
        print("ERROR: --threads must be >= 1", file=sys.stderr)
        return 2
    if args.api_port < 1 or args.api_port > 65535:
        print("ERROR: --api-port must be 1..65535", file=sys.stderr)
        return 2
    if args.print_time < 1:
        print("ERROR: --print-time must be >= 1", file=sys.stderr)
        return 2
    if args.organism_interval <= 0:
        print("ERROR: --organism-interval must be > 0", file=sys.stderr)
        return 2
    if args.organism_rounds < 1 or args.organism_rounds > 1000:
        print("ERROR: --organism-rounds must be 1..1000", file=sys.stderr)
        return 2

    print(
        f"[CYTHANX] profile={args.profile} "
        f"requested_threads={args.threads or 'auto'}",
        flush=True,
    )
    return supervise(args, fusion)


if __name__ == "__main__":
    raise SystemExit(main())
