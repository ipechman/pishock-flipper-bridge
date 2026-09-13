"""Inspect a built application, including compressed Python bytecode, before release."""
from __future__ import annotations

import argparse
import marshal
from pathlib import Path
import re
import struct
import sys
import types
import zipfile

from PyInstaller.archive.readers import CArchiveReader


class BundleAuditError(ValueError):
    pass


def _builder_prefixes(source: Path) -> set[str]:
    paths = (source.resolve(), Path(sys.executable).resolve(), Path.home())
    prefixes = set()
    for path in paths:
        text = str(path).replace('\\', '/')
        match = re.match(r'(?i)([a-z]:/users/[^/]+)', text)
        if match:
            prefixes.add(match.group(1))
    # A source checkout outside a user's home must not leak its location either.
    prefixes.add(str(source.resolve()).replace('\\', '/'))
    return prefixes


def audit_bundle(folder: Path, source: Path, forbidden: tuple[str, ...] = ()) -> dict[str, int]:
    folder = folder.resolve()
    prefixes = _builder_prefixes(source) | set(forbidden)
    needles = set()
    for prefix in prefixes:
        if len(prefix) < 4:
            raise BundleAuditError('Privacy match strings must be at least four characters.')
        for variant in (prefix, prefix.replace('/', '\\'), prefix.replace('\\', '/')):
            for encoding in ('utf-8', 'utf-16le'):
                needles.add(variant.lower().encode(encoding))
    counts = {'files': 0, 'code_objects': 0}

    def check_bytes(data: bytes, label: str) -> None:
        lowered = data.lower()
        if any(needle in lowered for needle in needles):
            # Never print the sensitive matching value.
            raise BundleAuditError(f'Private builder information found in {label}.')

    def check_code(code: object, label: str) -> None:
        if not isinstance(code, types.CodeType):
            return
        counts['code_objects'] += 1
        filename = code.co_filename
        if re.match(r'^(?:[a-zA-Z]:[/\\]|/|\\\\)', filename):
            raise BundleAuditError(f'Absolute Python source filename in {label}.')
        check_bytes(marshal.dumps(code), label)
        for constant in code.co_consts:
            if isinstance(constant, types.CodeType):
                check_code(constant, label)

    for path in sorted(folder.rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(folder)
        if any(part in {'.local', '.git', '__pycache__'} for part in relative.parts):
            raise BundleAuditError(f'Private/cache directory found in {relative}.')
        if path.suffix.lower() in {'.dpapi', '.log', '.pcap', '.pcapng', '.pdb'}:
            raise BundleAuditError(f'Unexpected private/debug artifact: {relative}.')
        counts['files'] += 1
        check_bytes(path.read_bytes(), str(relative))
        if path.name == 'base_library.zip':
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    data = archive.read(name)
                    check_bytes(data, name)
                    if name.endswith('.pyc'):
                        check_code(marshal.loads(data[16:]), name)

    exe = folder / 'PiShockBridge.exe'
    if not exe.is_file():
        raise BundleAuditError('The desktop executable is missing.')
    pe = exe.read_bytes()
    pe_offset = struct.unpack_from('<I', pe, 0x3C)[0]
    if struct.unpack_from('<H', pe, pe_offset + 24 + 68)[0] != 2:
        raise BundleAuditError('The executable must use the Windows GUI subsystem.')
    archive = CArchiveReader(str(exe))
    for name, entry in archive.toc.items():
        kind = entry[-1]
        if kind == 'z':
            pyz = archive.open_embedded_archive(name)
            for module in pyz.toc:
                check_code(pyz.extract(module), module)
        elif kind in {'s', 'm', 'M'}:
            check_code(marshal.loads(archive.extract(name)), name)
        elif kind in {'x', 'b'}:
            check_bytes(archive.extract(name), name)
    if not counts['code_objects']:
        raise BundleAuditError('No Python bytecode was audited.')
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('application', type=Path)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--forbid-text', action='append', default=[])
    options = parser.parse_args()
    result = audit_bundle(options.application, options.source, tuple(options.forbid_text))
    print(f"Privacy audit passed: {result['files']} files, {result['code_objects']} code objects.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
