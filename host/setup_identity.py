"""Explicit one-time import from a connected original owned hub."""
import argparse
from pathlib import Path
import sys

from identity import import_connected_identity, save_identity, load_identity
from radio import RadioError


def main(argv=None):
    parser = argparse.ArgumentParser(description='Save the selected original hub identity for this Windows account.')
    parser.add_argument('--hub-port', required=True)
    parser.add_argument('--hub-id', required=True, type=int)
    parser.add_argument('--shocker-id', required=True, type=int)
    args = parser.parse_args(argv)
    target = Path(__file__).resolve().parents[1] / '.local/device.dpapi'
    try:
        if target.exists():
            raise RadioError('A device identity is already saved here; it was not overwritten.')
        try:
            import serial  # Check the dependency before requesting hub information.
        except ImportError:
            print('USB support (pyserial) is missing. Use the same Python interpreter to run '
                  '-m pip install -r host/requirements.txt from the project folder. '
                  'See README.md for first-time setup.', file=sys.stderr)
            return 1
        selected = import_connected_identity(args.hub_port, args.hub_id, args.shocker_id)
        save_identity(target, selected)
        if load_identity(target) != selected:
            raise RadioError('Protected device identity did not verify.')
        print(f'Saved hub {selected.hub_id}, shocker {selected.shocker_id}, channel {selected.channel}.')
        print('The original hub can now be unplugged before starting the standalone bridge.')
        return 0
    except (RadioError, OSError):
        print('Could not import the selected hub identity. No raw device information was logged.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
