import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import systemd_smoke as smoke

def test_lifecycle_terminal_success():
    props = dict(InvocationID='a', LoadState='loaded', ExecMainPID='1', ExecMainStartTimestampMonotonic='1', ExecMainExitTimestampMonotonic='2', Result='success', ExecMainCode='1', ExecMainStatus='0', ActiveState='active', SubState='exited')
    smoke.validate_result(props, 'lifecycle', 'a')
