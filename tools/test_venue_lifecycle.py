import json
import os
from pathlib import Path
import pytest
from lib.claim_handoff import ENV_PLACE, ENV_OWNER, ENV_RUN_ID
from lib.labgrid_venue import ClientIdentity

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
    record = dict(place=bench.place, owner=owner, run_id=run_id, assertions=4)
    Path('/work/child.json').write_text(json.dumps(record))
    print(json.dumps(record), flush=True)
