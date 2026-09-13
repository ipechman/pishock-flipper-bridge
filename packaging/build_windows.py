"""Build an audited windowed application, portable ZIP, and per-user installer."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

from audit_bundle import audit_bundle


VERSION = '0.2.0'
SOURCE = Path(__file__).resolve().parents[1]
RUNTIME_FILES = (
    'host/desktop_app.py', 'host/desktop_service.py', 'host/desktop_paths.py',
    'host/flipper_install.py', 'host/device_backend.py', 'host/device_policy.py',
    'host/device_transport.py', 'host/identity.py', 'host/radio.py',
    'host/serial_mirror.py', 'host/setup_identity.py', 'host/standalone.py',
    'host/start_bridge.py',
    'packaging/tcl_runtime.py',
)
RESOURCE_FILES = (
    'pishock_usb_radio-official-1.4.3.fap', 'README.md', 'LICENSE',
    'NOTICE.md', 'docs/USER_GUIDE.md', 'assets/bridge.ico',
)
LICENSE_FILES = ('pyserial.txt', 'Tcl.txt', 'Tk.txt', 'OpenSSL.txt', 'README.md')


def run(arguments: list[str], **kwargs) -> None:
    subprocess.run(arguments, check=True, **kwargs)


def replace_generated_directory(source: Path, target: Path, output: Path) -> None:
    # Only one explicitly named generated application directory can be replaced.
    if target.is_symlink() or target.resolve().parent != output.resolve():
        raise ValueError('Refusing to replace an output outside the release directory.')
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def build(work: Path, output: Path, compiler: Path | None) -> dict[str, object]:
    if sys.platform != 'win32' or struct.calcsize('P') != 8:
        raise ValueError('Build releases on 64-bit Windows with 64-bit Python.')
    if sys.version_info[:2] != (3, 13):
        raise ValueError('Python 3.13 is the tested desktop release build target.')
    if importlib.metadata.version('pyinstaller') != '6.21.0':
        raise ValueError('Install the pinned dependencies from requirements-build.txt.')
    if importlib.metadata.version('pyserial') != '3.5':
        raise ValueError('The desktop release requires the pinned pyserial 3.5.')
    if compiler is not None and not compiler.is_file():
        raise ValueError('The Inno Setup compiler path does not exist.')
    if not re.fullmatch(r'\d+\.\d+\.\d+', VERSION):
        raise ValueError('The release version must use major.minor.patch.')
    work, output = work.resolve(), output.resolve()
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    for relative in RUNTIME_FILES + RESOURCE_FILES:
        if not (SOURCE / relative).is_file():
            raise ValueError(f'A required public build input is missing: {relative}')

    # A fresh allowlisted stage prevents local profiles, logs, caches, and other
    # checkout contents from becoming PyInstaller data files.
    with tempfile.TemporaryDirectory(prefix='release-', dir=work) as scratch:
        scratch = Path(scratch)
        stage = scratch / 'source'
        for relative in RUNTIME_FILES + RESOURCE_FILES:
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SOURCE / relative, destination)
        licenses = stage / 'licenses'
        licenses.mkdir()
        for name in LICENSE_FILES:
            shutil.copyfile(SOURCE / 'packaging/licenses' / name, licenses / name)
        python_license = next((Path(sys.base_prefix) / name for name in
                               ('LICENSE.txt', 'LICENSE_PYTHON.txt')
                               if (Path(sys.base_prefix) / name).is_file()), None)
        if python_license is None:
            raise ValueError('The Python distribution license was not found.')
        shutil.copyfile(python_license, licenses / 'Python.txt')
        pyinstaller_distribution = importlib.metadata.distribution('pyinstaller')
        pyinstaller_license = next((path for path in pyinstaller_distribution.files or ()
                                    if str(path).endswith('/licenses/COPYING.txt')), None)
        if pyinstaller_license is None:
            raise ValueError('The PyInstaller bootloader license was not found.')
        shutil.copyfile(pyinstaller_distribution.locate_file(pyinstaller_license),
                        licenses / 'PyInstaller.txt')
        version_info = scratch / 'version_info.txt'
        version_tuple = ', '.join(VERSION.split('.') + ['0'])
        version_info.write_text(
            'VSVersionInfo(ffi=FixedFileInfo(filevers=(' + version_tuple + '), '
            'prodvers=(' + version_tuple + '), mask=0x3f, flags=0x0, OS=0x40004, '
            "fileType=0x1, subtype=0x0, date=(0, 0)), kids=[StringFileInfo([StringTable('040904B0', ["
            "StringStruct('CompanyName', 'PiShock Bridge contributors'), "
            "StringStruct('FileDescription', 'PiShock Bridge desktop application'), "
            f"StringStruct('FileVersion', '{VERSION}'), "
            "StringStruct('InternalName', 'PiShockBridge'), "
            "StringStruct('OriginalFilename', 'PiShockBridge.exe'), "
            "StringStruct('ProductName', 'PiShock Bridge'), "
            f"StringStruct('ProductVersion', '{VERSION}')])]), "
            "VarFileInfo([VarStruct('Translation', [1033, 1200])])])\n",
            encoding='utf-8',
        )
        command = [
            sys.executable, '-B', '-m', 'PyInstaller', '--clean', '--noconfirm',
            '--onedir', '--windowed', '--noupx', '--name', 'PiShockBridge',
            '--distpath', str(scratch / 'dist'), '--workpath', str(scratch / 'build'),
            '--specpath', str(scratch), '--paths', str(stage / 'host'),
            '--version-file', str(version_info),
            '--icon', str(stage / 'assets/bridge.ico'),
            '--runtime-hook', str(stage / 'packaging/tcl_runtime.py'),
            '--exclude-module', 'pytest', '--exclude-module', 'unittest',
        ]
        for relative in RESOURCE_FILES:
            parent = str(Path(relative).parent)
            command += ['--add-data', f'{stage / relative};{parent}']
        command += ['--add-data', f'{licenses};licenses']
        command.append(str(stage / 'host/desktop_app.py'))
        environment = os.environ.copy()
        environment['PYTHONDONTWRITEBYTECODE'] = '1'
        environment['PYINSTALLER_CONFIG_DIR'] = str(work / 'pyinstaller-cache')
        run(command, cwd=stage, env=environment)
        built_application = scratch / 'dist/PiShockBridge'
        audit = audit_bundle(built_application, SOURCE)
        # The app's diagnostic mode only verifies bundled imports/resources and
        # creates/destroys a hidden window. It never reads a saved identity,
        # enumerates serial devices, connects to PiShock, or transmits RF.
        run([str(built_application / 'PiShockBridge.exe'), '--self-test'], timeout=30)

        release_name = f'PiShockBridge-{VERSION}-windows-x64'
        application = output / release_name
        replace_generated_directory(built_application, application, output)
        portable = output / f'{release_name}.zip'
        with zipfile.ZipFile(portable, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(application.rglob('*')):
                if path.is_file():
                    archive.write(path, f'{release_name}/{path.relative_to(application).as_posix()}')
        artifacts = [portable]
        if compiler is not None:
            run([
                str(compiler), '/Qp', f'/DAppDir={application}',
                f'/DOutputDir={output}', f'/DAppVersion={VERSION}',
                str(SOURCE / 'packaging/installer.iss'),
            ])
            artifacts.append(output / f'PiShockBridge-Setup-{VERSION}.exe')
        hashes = '\n'.join(
            f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}' for path in artifacts
        ) + '\n'
        (output / 'SHA256SUMS.txt').write_text(hashes, encoding='ascii')
        result = {
            'version': VERSION, 'python': '.'.join(map(str, sys.version_info[:3])),
            'pyinstaller': '6.21.0', 'application': application.name,
            'artifacts': [path.name for path in artifacts], 'privacy_audit': audit,
            'diagnostic_launch': 'passed', 'installer_signed': False,
        }
        (output / 'BUILD_INFO.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir', type=Path, default=SOURCE / 'build/desktop')
    parser.add_argument('--output-dir', type=Path, default=SOURCE / 'dist')
    parser.add_argument('--inno-compiler', type=Path,
                        help='ISCC.exe path; omit to build only the portable application and ZIP.')
    options = parser.parse_args()
    print(json.dumps(build(options.work_dir, options.output_dir, options.inno_compiler), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
