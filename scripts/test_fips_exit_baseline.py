"""Offline checks against captured kernel data, not new packet evidence."""
import copy
import json
from pathlib import Path
import runpy
import unittest

ROOT = Path(__file__).resolve().parents[1]

class BaselineTests(unittest.TestCase):
    def test_plan_identity_and_mutations(self):
        records = [json.loads(line) for line in
                   (ROOT / 'results/fips-recovery-slice/packet.log').read_text().splitlines()
                   if line.startswith('{')]
        baseline = next(r['kernel_observations'] for r in records
                        if r.get('tunnel_state') == 'installed')
        links = json.loads(baseline['raw']['links'])
        tunnel = next(x for x in links if x['ifname'] == 'exit-test')
        addresses = json.loads(baseline['raw']['addresses'])
        address = next(x for x in addresses if x['ifname'] == 'exit-test')
        # Expected fixture identities from the pinned renderer's constant keys.
        payload = dict(outer='fd01:d0fa:bd25:1fcb:be2b:93b4:b927:b26a',
                       peer_outer='fd57:78f9:85db:754c:6628:691f:56fa:dae5',
                       inner='100.65.208.250')
        validate = runpy.run_path(str(ROOT / 'scripts/fips-exit-routes.py')).get('verify_fixture_baseline')
        self.assertTrue(callable(validate), 'baseline validator missing')
        validate(baseline, payload)
        for key, value in [('outer', '::1'), ('peer_outer', '::1'), ('inner', '127.0.0.1')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate(baseline, dict(payload, **{key: value}))
        for section, field, value in [('links', 'mtu', 1500), ('links', 'ifindex', 0),
                                      ('addresses', 'addr_info', [])]:
            changed = copy.deepcopy(baseline)
            data = json.loads(changed['raw'][section])
            target = next(x for x in data if x['ifname'] == 'exit-test')
            target[field] = value
            changed['raw'][section] = json.dumps(data)
            with self.subTest(section=section, field=field), self.assertRaises(ValueError):
                validate(changed, payload)

if __name__ == '__main__':
    unittest.main()
