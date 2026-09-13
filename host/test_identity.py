import json
from pathlib import Path
import sys
import tempfile
import unittest

import identity
from radio import RadioError


def sample():
    return {'macAddress': 'AA:BB:CC:DD:EE:FF', 'clientId': 4100,
            'claimed': True, 'type': 3, 'ownerId': 99, 'publicIp': '203.0.113.10',
            'shockers': [{'id': 1234, 'type': 1, 'paused': False}],
            'networks': [{'password': 'DO_NOT_RETAIN'}], 'otk': 'DO_NOT_RETAIN'}


class IdentityTests(unittest.TestCase):
    def test_info_retains_only_identity(self):
        result = identity.identity_from_info(sample(), 4100, 1234)
        self.assertEqual(result.hub_id, 4100)
        self.assertNotIn('DO_NOT_RETAIN', json.dumps(identity.asdict(result)))
        self.assertNotIn('AA:BB:', repr(result))

    def test_mismatched_devices_rejected(self):
        for hub, shocker in [(22, 1234), (4100, 33)]:
            with self.assertRaises(RadioError):
                identity.identity_from_info(sample(), hub, shocker)

    def test_unclaimed_and_wrong_type_rejected(self):
        for field, value in [('claimed', False), ('type', 1), ('ownerId', 0),
                             ('macAddress', 'invalid'), ('publicIp', 'not an address')]:
            info = sample()
            info[field] = value
            with self.subTest(field=field), self.assertRaises(RadioError):
                identity.identity_from_info(info, 4100, 1234)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows DPAPI profile')
    def test_profile_is_protected_and_round_trips(self):
        data = identity.identity_from_info(sample(), 4100, 1234)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'device.dpapi'
            identity.save_identity(target, data)
            self.assertNotIn(data.mac.encode(), target.read_bytes())
            self.assertEqual(identity.load_identity(target), data)
            with self.assertRaises(FileExistsError):
                identity.save_identity(target, data)


if __name__ == '__main__':
    unittest.main()
