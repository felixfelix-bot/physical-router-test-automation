import json
import os
from pathlib import Path
import time
import pytest
from lib.claim_handoff import ENV_PLACE, ENV_OWNER, ENV_RUN_ID
from lib.labgrid_venue import ClientIdentity

#: Hold the place for this long after acquiring it. Set by the managed trial
#: scenarios only, so a child can outlive a 2 s lease (``renew``) or stay inside
#: the venue while a second runner is refused (``contention``). 0 (the default)
#: keeps the measured lifecycle trial identical in behaviour.
SLEEP_S = float(os.environ.get('TOLLGATE_TRIAL_SLEEP_S', '0') or 0)

@pytest.mark.venue_only
def test_real_venue_ownership(venue_ownership):
    bench = venue_ownership.bench
    run_id = os.environ[ENV_RUN_ID]
    expected = ClientIdentity.for_run(run_id, host='trial').owner
    owner = bench.owner()
    assert bench.acquired
    assert bench.place == os.environ[ENV_PLACE] == 'empty'
    assert owner == os.environ[ENV_OWNER] == expected
    assert run_id == os.environ['TOLLGATE_RUN_ID']
    started = time.monotonic()
    if SLEEP_S > 0:
        time.sleep(SLEEP_S)
    duration_s = time.monotonic() - started
    record = dict(place=bench.place, owner=owner, run_id=run_id, assertions=4,
                  sleep_s=SLEEP_S, duration_s=duration_s)
    Path('/work/child.json').write_text(json.dumps(record))
    print(json.dumps(record), flush=True)
    if SLEEP_S > 0:
        # Only reached if the venue kept the place for the whole hold window: a
        # lease lost mid-run terminates this process group before this line, so
        # the absence of this marker is the "child was killed" evidence.
        print(json.dumps({'finished': True, 'duration_s': duration_s}), flush=True)
