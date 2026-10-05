"""Build the generic controller using an already installed f7 SDK/toolchain.

Only public allowlisted sources enter staging. No downloads, device access, or
personal configuration are part of this build.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys


def command(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, **kwargs)


def sections(data: bytes) -> dict[str, bytes]:
    if data[:7] != b"\x7fELF\x01\x01\x01" or struct.unpack_from("<H", data, 18)[0] != 40:
        raise ValueError("Controller must be a little-endian ARM ELF32 file")
    offset = struct.unpack_from("<I", data, 32)[0]
    size, count, names_index = struct.unpack_from("<HHH", data, 46)
    headers = [struct.unpack_from("<10I", data, offset + i * size) for i in range(count)]
    names_header = headers[names_index]
    names = data[names_header[4]:names_header[4] + names_header[5]]
    result = {}
    for header in headers:
        start = header[0]
        name = names[start:names.index(0, start)].decode("ascii")
        result[name] = data[header[4]:header[4] + header[5]] if header[1] != 8 else b""
    return result


def verify_fap(path: Path, api_file: Path, nm: Path, forbidden: list[Path]) -> tuple[str, int]:
    with api_file.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    api = next(row["name"] for row in rows if row["entry"] == "Version")
    if api not in {"87.1", "88.9"}:
        raise ValueError(f"Unsupported controller SDK API: {api}")
    major, minor = map(int, api.split("."))
    data = path.read_bytes()
    parts = sections(data)
    metadata = parts.get(".fapmeta", b"")
    if len(metadata) != 85 or struct.unpack_from("<IIIh", metadata) != (0x52474448, 1, (major << 16) | minor, 7):
        raise ValueError("Controller FAP metadata does not match SDK/f7")
    if metadata[20:52].split(b"\0", 1)[0] != b"PiShock Controller":
        raise ValueError("Controller FAP application name mismatch")
    if any(name.startswith(".debug") or name == ".gnu_debuglink" for name in parts):
        raise ValueError("Controller release still contains debug sections")
    for prefix in forbidden:
        for spelling in {str(prefix), prefix.as_posix()}:
            if spelling.encode().lower() in data.lower():
                raise ValueError("Controller release contains an absolute builder path")
    imports = command([str(nm), "--undefined-only", str(path)], capture_output=True, text=True).stdout
    symbols = {line.split()[-1] for line in imports.splitlines() if line.strip()}
    available = {row["name"] for row in rows if row["status"] == "+" and row["entry"] in {"Function", "Variable"}}
    unresolved = symbols - available
    if unresolved:
        raise ValueError("Controller imports missing from SDK: " + ", ".join(sorted(unresolved)))
    if any("usb_cdc" in symbol for symbol in symbols):
        raise ValueError("Standalone controller must not import USB CDC APIs")
    return api, len(symbols)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", required=True, type=Path)
    parser.add_argument("--toolchain", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sdk, toolchain, work, output = (getattr(args, name).resolve() for name in ("sdk", "toolchain", "work_dir", "output"))
    scripts = sdk / "current/scripts/ufbt"
    api_file = sdk / "current/sdk_headers/f7_sdk/targets/f7/api_symbols.csv"
    binaries = toolchain / "toolchain/x86_64-windows/bin"
    suffix = ".exe" if os.name == "nt" else ""
    objcopy = binaries / f"arm-none-eabi-objcopy{suffix}"
    nm = binaries / f"arm-none-eabi-nm{suffix}"
    if not all(path.exists() for path in (scripts / "SConstruct", api_file, objcopy, nm)):
        raise SystemExit("Use an existing complete SDK state and ARM toolchain; no tools will be downloaded")
    app = work / "controller-source"
    app.mkdir(parents=True, exist_ok=True)
    allowlist = {
        "application.fam": root / "controller/application.fam",
        "pishock_controller.c": root / "controller/pishock_controller.c",
        "controller_core.c": root / "controller/controller_core.c",
        "controller_core.h": root / "controller/controller_core.h",
        "radio_core.c": root / "app/radio_core.c",
        "radio_core.h": root / "app/radio_core.h",
    }
    # Reuse only a clean source stage: extra files could be pulled into a FAP.
    extras = set(path.name for path in app.iterdir()) - set(allowlist) - {"dist", ".vscode"}
    if extras:
        raise SystemExit("Controller staging contains unrecognized files; choose a fresh --work-dir")
    for name, source in allowlist.items():
        shutil.copyfile(source, app / name)
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join((str(binaries), str(toolchain / "toolchain/x86_64-windows/python"), env["PATH"]))
    env["UFBT_STATE_DIR"] = str(sdk)
    env["FBT_TOOLCHAIN_PATH"] = str(toolchain)
    env["PYTHONNOUSERSITE"] = "1"
    # SDK GatherSources deduplicates patterns with a set. Fix its iteration
    # order so object/link ordering is stable across fresh Python processes.
    env["PYTHONHASHSEED"] = "0"
    build_python = toolchain / "toolchain/x86_64-windows/python/python.exe"
    if not build_python.exists():
        build_python = Path(sys.executable)
    command([str(build_python), "-m", "SCons", "-Q", "-C", str(scripts),
             "UFBT_APP_DIR=" + str(app), "STRICT_FAP_IMPORT_CHECK=1"], env=env)
    built = app / "dist/pishock_controller.fap"
    release = work / "pishock_controller-release.fap"
    # SDK strips debug data but retains a CRC-bearing debuglink. Remove it so
    # paths in its external debug ELF cannot affect generic release bytes.
    command([str(objcopy), "--remove-section=.gnu_debuglink", "--strip-debug", str(built), str(release)])
    api, imports = verify_fap(release, api_file, nm, [Path.home(), root, work, sdk, toolchain])
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(release, output)
    print(f"Verified PiShock Controller: ARM/f7, API {api}, {imports} SDK imports, no CDC/debug/builder paths")
    print(f"SHA256 {hashlib.sha256(output.read_bytes()).hexdigest()}  {output.name}")


if __name__ == "__main__":
    main()
