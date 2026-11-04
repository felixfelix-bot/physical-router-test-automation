"""One private coordinator and actual runner invocations; never a DUT.

Scenario selection is by the documented env var ``TOLLGATE_TRIAL_SCENARIO``
(exported by ``tools/systemd_smoke_child.py --scenario``):

``baseline`` (default)
    The measured lifecycle trial: unauthorized manifest refused with rc 75, then
    one authorized run that acquires, passes, and releases cleanly.
``renew``
    TTL 2 s / heartbeat ~0.67 s with a child that runs ~7 s: the lease must be
    renewed at least twice *in the store* and must never lapse, and the child
    must still be alive at its own exit.
``renewal_fail``
    The claim is fenced while the child runs: supervision must lose the
    authority (exit 77), terminate the child's group, and leave the lane NOT
    clean-released.
``renewal_expire``
    The store expires the allocation into quarantine while the child runs: the
    next beat sees a fenced/quarantined claim (exit 77), the child's group is
    terminated, and the lane stays occupied.
``contention``
    A second runner for the SAME place/lane, with a distinct run id and owner,
    must be refused *before* acquisition while the first still holds it, and
    must not release the first runner's place.
``crash_run``
    The runner process group is SIGKILLed mid-run, simulating a crashed or
    disconnected runner while its lease is still unexpired and its venue child
    is still alive. Five things must hold, all read from the real SQLite store
    and the real coordinator: (a) the lane is NOT reassignable while the dead
    holder's lease is unexpired; (b) after real TTL expiry the lane is
    ``quarantined``, never handed out on the strength of a timestamp; (c) a
    second runner is refused with a durable ``lane_quarantined: lease_expired``
    reason; (d) the dead holder's ``(run_id, fence)`` cannot renew, activate,
    release or abort — the fencing generation moved; (e) the place is still
    owned by the dead run after death and no clean release was ever recorded.

Nothing here acquires a place on the runner's behalf: the pytest venue fixture
owns it (``tests/conftest.py``). The harness only reads ownership and the
authority ledger.
"""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
sys.path.insert(0, '/source')
from lib.fleet_authority import Fenced, FleetAuthority, HostSpec, LaneSpec, State  # noqa: E402
from lib.labgrid_venue import ClientIdentity, LabgridBench  # noqa: E402

PY = '/runtime/bin/python3'
COORD = '127.0.0.1:20408'

#: Extra pytest arguments handed to run-profile.py. No bare ``--``: run-profile
#: forwards everything after its own flags verbatim (``parse_known_args``), so a
#: separator reaches pytest as a stray *path* and pytest exits 4 with
#: ``file or directory not found: --no-dut`` before collecting anything.
RUNNER_EXTRA = ['--no-dut', '--preflight=off', '-c', '/dev/null',
                '-p', 'pytest_metadata.plugin', '-p', 'pytest_html.plugin',
                '-p', 'no:cacheprovider', '-s']

#: Lease TTL for the renewal scenario. Small on purpose: the child must outlive
#: it several times over, and the supervisor's default heartbeat is ``ttl/3``
#: (0.67 s), so a 7 s child forces ~9 real renewals.
RENEW_TTL_S = 2.0
#: How long the venue child sleeps once it owns the place.
RENEW_CHILD_SLEEP_S = 7.0
#: Long enough that a child which was *not* terminated could never finish
#: before the supervisor's own teardown deadline.
FAIL_CHILD_SLEEP_S = 30.0
#: Small enough that a real renewal is observed before the revoke fires
#: (heartbeat is ``ttl/3``), large enough that the lease cannot lapse first.
FAIL_TTL_S = 6.0
#: A refusal must be a supervision exit, never a test verdict.
SUPERVISION_EXITS = (76, 77)

#: Every scenario this harness implements. The managed service's child asserts
#: membership against THIS list, so an added scenario cannot silently fall back
#: to the baseline run.
SCENARIOS = ('baseline', 'renew', 'renewal_fail', 'renewal_expire', 'contention',
             'crash_run')

#: Refusals that mean "someone else holds this place/lane", i.e. the second
#: runner was stopped before it could acquire anything.
CONTENTION_REASONS = (
    'lane_busy', 'domain_conflict', 'resource_vector_insufficient',
    'host_resource_vector_insufficient', 'lane_quarantined',
)

#: Lease TTL for ``crash_run``. The runner is SIGKILLed well inside it, so the
#: store still believes the (dead) holder owns the lane; the harness then waits
#: this long *plus* a margin so expiry is caused by real wall-clock time, not by
#: an injected ``expire(now=future)``. 20 s rather than something smaller: the
#: pre-expiry reassignment attempt spawns a *real* ``run-profile.py`` (heavy
#: import), and that refusal must land while the lease is still unexpired or the
#: scenario silently degenerates into the post-expiry case.
CRASH_TTL_S = 20.0
#: The venue child must outlive the kill AND the expiry wait: it is the "old
#: processes may still be mutating a DUT" that makes reassignment unsafe. 45 s
#: fits the unchanged 120 s RuntimeMaxSec window (kill at ~2 s, expiry at ~10 s,
#: second-runner refusal at ~12 s, teardown afterwards — the child dies with the
#: unit).
CRASH_CHILD_SLEEP_S = 45.0
#: Seconds past the TTL to wait before declaring the lease expired, so the
#: expiry is the store's own clock crossing, not a race.
CRASH_EXPIRY_MARGIN_S = 2.5

def bounded(value, limit=8192):
    """Keep a fixed-size tail; a hang must not turn into an evidence gap."""
    value = value or ''
    if isinstance(value, bytes):
        value = value.decode('utf-8', errors='replace')
    return value[-limit:], len(value) > limit


def command(argv, env, timeout=65, cwd='/work'):
    """Run one argv and ALWAYS journal argv, rc and both streams.

    ``subprocess.run`` raises ``TimeoutExpired`` carrying whatever the child had
    already written; the previous version let that exception escape unrecorded,
    so a timed-out client left no output at all. The timeout stays a hard
    failure — this only stops the evidence from being discarded.
    """
    try:
        result = subprocess.run(argv, env=env, cwd=cwd, text=True,
                                capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        out, out_cut = bounded(exc.stdout)
        err, err_cut = bounded(exc.stderr)
        print(json.dumps(dict(argv=argv, rc=None, timeout=timeout, stdout=out,
                              stdout_truncated=out_cut, stderr=err,
                              stderr_truncated=err_cut)), flush=True)
        raise
    print(json.dumps(dict(argv=argv, rc=result.returncode, stdout=result.stdout, stderr=result.stderr)), flush=True)
    return result


# --------------------------------------------------------------------------- #
# pure verdicts (pinned by tools/test_fleet_trial_scenarios.py, no I/O)
# --------------------------------------------------------------------------- #
def renewal_evidence(samples, *, ttl_s, exited_at):
    """Prove a long child stayed leased: count real extensions, find lapses.

    A "renewal" is an observed increase of the allocation's ``expires_at`` in
    the authority store — the only externally visible trace ``renew()`` leaves.
    Nothing is inferred: no samples is a refusal, not an empty pass.
    """
    live = [s for s in samples if s.get('state') in ('reserved', 'active') and s.get('expires_at') is not None]
    assert live, 'no lease samples: a lease nobody observed is not evidence'
    assert float(ttl_s) > 0
    renewals = 0
    lapsed = 0
    for previous, current in zip(live, live[1:]):
        if float(current['expires_at']) > float(previous['expires_at']):
            renewals += 1
        if float(current['expires_at']) - float(current['t']) <= 0:
            lapsed += 1
    if float(live[0]['expires_at']) - float(live[0]['t']) <= 0:
        lapsed += 1
    return dict(
        samples=len(live),
        renewals_observed=renewals,
        lapsed_samples=lapsed,
        first_expiry=float(live[0]['expires_at']),
        last_expiry=float(live[-1]['expires_at']),
        # Would the ORIGINAL lease have run out while the child was still
        # working? If not, this scenario proves nothing about renewal.
        would_have_expired_before_exit=float(live[0]['expires_at']) < float(exited_at),
        # The lease was still valid at the LAST observation (i.e. no sample
        # caught it lapsed). Compared against the sample's own clock, not the
        # exit time: the last beat may legitimately be up to one heartbeat
        # before the child exits.
        live_at_exit=float(live[-1]['expires_at']) > float(live[-1]['t']),
    )


def fatal_renewal_verdict(*, exit_code, release_state, marker_seen, duration_s,
                          child_sleep_s, renewals_observed):
    """A lost lease is only proven by a dead child and an occupied lane."""
    return dict(
        exit_is_supervision=int(exit_code) in SUPERVISION_EXITS,
        child_terminated=(not marker_seen) and float(duration_s) < 0.5 * float(child_sleep_s),
        lane_not_cleanreleased=str(release_state) != 'clean_released',
        renewed_before_loss=int(renewals_observed) >= 1,
        duration_s=float(duration_s),
        child_sleep_s=float(child_sleep_s),
    )


def assert_fatal_renewal(verdict):
    assert verdict['exit_is_supervision'], 'a lost lease must exit 76/77, not a test verdict'
    assert verdict['child_terminated'], 'the child was not terminated after the lease was lost'
    assert verdict['lane_not_cleanreleased'], 'the lane was reported clean-released after a lost lease'
    assert verdict['renewed_before_loss'], 'no renewal happened before the loss; supervision is unproven'


def contention_verdict(*, rc_b, stderr_b, owner_a, owner_b, owner_before,
                       owner_after, owner_final, run_dir_b_exists,
                       child_b_exists, allocations_for_b, first_still_running):
    """A second runner for one place is refused without touching that place.

    ``run_dir_b_exists`` is *allowed*: ``run-profile.py`` creates the results
    layout before it asks for a reservation, so a refusal at admission leaves an
    empty run directory behind. That directory is not an acquisition — the
    place belongs to the pytest venue fixture, which never ran for B. What must
    be absent is B's child record and B's allocation.
    """
    reason = ''
    for line in (stderr_b or '').splitlines():
        marker = 'ADMISSION REFUSED: '
        if marker in line:
            reason = line.split(marker, 1)[1].split(':', 1)[0].strip()
            break
    return dict(
        refusal_reason=reason,
        # 75 is ADMISSION_EXIT: refused before spawn, before any child existed
        # and before the lane was reserved.
        refused_before_acquisition=(int(rc_b) == 75 and reason in CONTENTION_REASONS
                                    and not child_b_exists and not allocations_for_b),
        # The first runner still owned the place before AND right after B was
        # refused, and it was only freed by A's own release at the end.
        no_shared_place_release=(owner_a != '' and owner_before == owner_a
                                 and owner_after == owner_a and owner_final == ''),
        distinct_owners=(owner_a != '' and owner_b != '' and owner_a != owner_b),
        first_still_running=bool(first_still_running),
    )


def assert_contention(verdict):
    assert verdict['distinct_owners'], 'both runners resolved to the same coordinator owner'
    assert verdict['refused_before_acquisition'], 'the second runner was not refused before acquisition'
    assert verdict['first_still_running'], 'the first runner had already exited when the second was refused'
    assert verdict['no_shared_place_release'], 'the second runner changed the first place ownership'


def crash_release_verdict(
    *, state_before_kill, expires_at_before_kill, killed_at, state_after_expiry,
    lane_quarantined, quarantine_reason, reassign_while_unexpired,
    reassign_reason, second_refusal_reason, second_rc, second_child_exists,
    second_allocation_exists, stale_refusals, fence_before, fence_after,
    owner_after_death, owner_a, release_recorded, allocations_for_a,
    expiry_events=0, released_events=0,
):
    """The five crash/disconnect invariants, as booleans over observed facts.

    Every argument is something the harness *read* — from the SQLite store, the
    coordinator, or a real refused call. Nothing is derived from an assumption
    about what the code should do:

    ``state_before_kill`` / ``expires_at_before_kill`` / ``killed_at``
        the dead holder's claim at the last sample before the SIGKILL, and the
        wall clock at the kill. Together they say the lease was still unexpired
        when the runner died, which is the precondition for (a).
    ``reassign_while_unexpired`` / ``reassign_reason``
        what a real second admission attempt got *while* the dead holder's lease
        was still unexpired. It must be a refusal, and never a grant.
    ``state_after_expiry`` / ``lane_quarantined`` / ``quarantine_reason``
        the store's own state after real TTL expiry, plus the lane-level
        quarantine row. (b) needs ``quarantined``, not ``available``.
    ``second_refusal_reason`` / ``second_rc`` / ``second_child_exists`` /
    ``second_allocation_exists``
        the *real runner's* refusal after expiry: durable reason, no child, no
        allocation. (c).
    ``stale_refusals``
        ``{operation: exception-class-or-''}`` from the dead holder's own
        ``(run_id, old_fence)`` replayed against the store. (d) requires every
        one of them to be a ``Fenced``/refusal, never a success.
    ``fence_before`` / ``fence_after``
        the fencing generation at grant and after expiry. A generation that did
        not move would leave the zombie's token valid.
    ``owner_after_death`` / ``owner_a``
        coordinator ownership read after the runner died. (e) requires it to
        still be the dead run's owner string.
    ``release_recorded``
        the dead run never wrote a clean release: no ``release.json`` claims
        ``clean_released`` for it. ``expiry_event_seq`` / ``released_event_seq``
        are the store's own event-log rows: the expiry must be recorded as
        ``lease_expired`` and the run must have no ``released`` row at all.
    """
    return dict(
        # (a) the lane is not handed out while an unexpired (dead) claim holds it
        lease_unexpired_at_kill=float(expires_at_before_kill) > float(killed_at),
        holder_state_at_kill=str(state_before_kill),
        expires_at_at_kill=float(expires_at_before_kill),
        killed_at=float(killed_at),
        not_reassignable_while_unexpired=(not reassign_while_unexpired),
        reassign_refusal_reason=str(reassign_reason or ''),
        # (b) real expiry quarantines, never frees
        state_after_expiry=str(state_after_expiry),
        lane_quarantined=bool(lane_quarantined),
        quarantine_reason=str(quarantine_reason or ''),
        expired_into_quarantine=(str(state_after_expiry) == State.QUARANTINED.value
                                 and bool(lane_quarantined)),
        # (c) a second runner is refused, durably and without acquiring
        second_refusal_reason=str(second_refusal_reason or ''),
        second_refused=(int(second_rc) == 75
                        and str(second_refusal_reason or '') in CONTENTION_REASONS
                        and not second_child_exists and not second_allocation_exists),
        # The reason must be DURABLE, not just this process's opinion: the store
        # must carry a `lease_expired` event for this run and no `released` one.
        expiry_events=int(expiry_events),
        released_events=int(released_events),
        refusal_reason_is_durable=(
            int(expiry_events) >= 1 and int(released_events) == 0
            and str(second_refusal_reason or '') == 'lane_quarantined'
            and str(quarantine_reason or '') == 'lease_expired'),
        # (d) the stale holder's token authenticates nothing
        stale_refusals=dict(stale_refusals),
        fence_before=int(fence_before),
        fence_after=int(fence_after),
        fence_advanced=int(fence_after) > int(fence_before),
        stale_token_refused=bool(stale_refusals)
                             and all(v not in ('', None, 'ACCEPTED')
                                     for v in stale_refusals.values()),
        # (e) the dead runner's ownership is visible, not silently clean
        owner_after_death=str(owner_after_death or ''),
        owner_a=str(owner_a or ''),
        owner_visible_after_death=(str(owner_after_death or '') != ''
                                   and str(owner_after_death) == str(owner_a)),
        release_recorded=bool(release_recorded),
        clean_release_not_claimed=not release_recorded and bool(allocations_for_a),
    )


def assert_crash_release(verdict):
    """RED-first gate: every one of (a)-(e) is its own assertion.

    Ordered (b) and (d) first because they are the safety-critical invariants:
    a lane handed out on a timestamp, or a zombie that can still renew, is worse
    than a harness that merely mis-reads ownership.
    """
    assert verdict['lease_unexpired_at_kill'], (
        'the lease had already expired when the runner was killed; '
        f"expires_at={verdict['holder_state_at_kill']} not > killed_at")
    # (b)
    assert verdict['expired_into_quarantine'], (
        'expiry did not quarantine the lane: '
        f"state_after_expiry={verdict['state_after_expiry']!r} "
        f"lane_quarantined={verdict['lane_quarantined']}")
    # (d)
    assert verdict['fence_advanced'], (
        f"fencing generation did not advance ({verdict['fence_before']} -> "
        f"{verdict['fence_after']}): a dead holder's token would still authenticate")
    assert verdict['stale_token_refused'], (
        f"a stale (run_id, fence) was accepted: {verdict['stale_refusals']}")
    # (a)
    assert verdict['not_reassignable_while_unexpired'], (
        f"the lane was reassignable while the dead holder's lease was unexpired "
        f"(reason={verdict['reassign_refusal_reason']!r})")
    # (c)
    assert verdict['second_refused'], (
        f"the second runner was not durably refused (rc/reason/child/allocation): "
        f"reason={verdict['second_refusal_reason']!r}")
    assert verdict['refusal_reason_is_durable'], (
        'the refusal was not backed by durable store events: '
        f"expiry_events={verdict['expiry_events']} "
        f"released_events={verdict['released_events']} "
        f"quarantine_reason={verdict['quarantine_reason']!r} "
        f"runner_reason={verdict['second_refusal_reason']!r}")
    # (e)
    assert verdict['owner_visible_after_death'], (
        'the dead runner\'s place ownership was not visible after death: '
        f"owner_after_death={verdict['owner_after_death']!r} owner_a={verdict['owner_a']!r}")
    assert verdict['clean_release_not_claimed'], (
        'a clean release was recorded for a runner that was killed mid-run')


def stale_transition_outcomes(authority, run_id, fence):
    """Replay the dead holder's own token against the REAL store.

    Each entry is the exception class name, or ``'ACCEPTED'`` for the failure
    mode that matters: a zombie whose ``(run_id, fence)`` still authenticates.
    """
    outcomes = {}

    def record(name, call):
        try:
            call()
        except Exception as exc:  # noqa: BLE001 - the class name IS the evidence
            outcomes[name] = type(exc).__name__
        else:
            outcomes[name] = 'ACCEPTED'

    record('renew', lambda: authority.renew(run_id, fence, ttl_s=30.0))
    record('activate', lambda: authority.activate(run_id, fence, note='trial: zombie'))
    record('release', lambda: authority.complete(run_id, fence, clean=True,
                                                 detail='trial: zombie release'))
    record('abort', lambda: authority.abort(run_id, fence, detail='trial: zombie abort'))
    return outcomes


def place_owner(bench):
    """The coordinator's owner string for the place, or '' when free."""
    try:
        return bench.owner()
    except Exception as exc:  # noqa: BLE001 - an unreadable owner is not clean
        return f'<unreadable: {type(exc).__name__}'


# --------------------------------------------------------------------------- #
# live observation of the authority store
# --------------------------------------------------------------------------- #
def sample_lease(authority, run_id):
    """Read the allocation as an outside observer; never renew or mutate."""
    now = time.time()
    try:
        allocation = authority.allocation(run_id)
    except Exception as exc:  # noqa: BLE001 - a vanished run is a sample too
        return dict(t=now, expires_at=None, state='missing', fence=None,
                    error=type(exc).__name__)
    return dict(t=now, expires_at=float(allocation.expires_at), state=allocation.state.value,
                fence=int(allocation.fence))


def refusal_reason(stderr):
    """The stable machine reason from an ``ADMISSION REFUSED: <reason>: ...`` line."""
    for line in (stderr or '').splitlines():
        marker = 'ADMISSION REFUSED: '
        if marker in line:
            return line.split(marker, 1)[1].split(':', 1)[0].strip()
    return ''


def _renewals(samples):
    return sum(1 for a, b in zip(samples, samples[1:])
               if a.get('expires_at') is not None and b.get('expires_at') is not None
               and b['expires_at'] > a['expires_at'])


def run_watched(argv, env, *, authority, run_id, action=None, trigger=None,
                timeout=120, interval=0.1):
    """Run a runner like ``command()`` while sampling its live lease.

    ``action(samples)`` fires once, as soon as ``trigger(samples, latest)`` says
    the run has reached a state worth disturbing — that is how a mid-run loss is
    caused deterministically instead of by sleeping and hoping.
    """
    print(json.dumps(dict(argv=argv, watched=True, run_id=run_id)), flush=True)
    started = time.time()
    process = subprocess.Popen(argv, env=env, cwd='/work', text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    samples = []
    fired = False
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None and time.monotonic() < deadline:
            latest = sample_lease(authority, run_id)
            samples.append(latest)
            if action is not None and not fired and trigger is not None and trigger(samples, latest):
                action(samples)
                fired = True
            time.sleep(interval)
        if process.poll() is None:
            process.kill()
            raise AssertionError(f'runner {run_id!r} did not finish within {timeout}s')
        out, err = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    exited_at = time.time()
    record = dict(argv=argv, rc=process.returncode, stdout=out, stderr=err,
                  renewals_observed=_renewals(samples),
                  action_fired=fired, started_at=started, exited_at=exited_at,
                  samples=samples[-400:])
    print(json.dumps(record), flush=True)
    return dict(record, samples=samples)


def surviving_run_processes(run_dir):
    """Any process still running with this run's directory on its command line."""
    hits = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / 'cmdline').read_bytes().decode('utf-8', 'replace')
        except OSError:
            continue
        if run_dir in cmdline:
            hits.append(int(entry.name))
    return hits


# --------------------------------------------------------------------------- #
# fixtures: one private coordinator, one authority, no DUT
# --------------------------------------------------------------------------- #
def prepare(ctx):
    import yaml
    env = dict(os.environ, HOME='/work', TMPDIR='/work', PYTHONDONTWRITEBYTECODE='1',
               PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONPATH='/source',
               TOLLGATE_BENCH_LOCK='/work/bench.lock', TOLLGATE_SKIP_ROUTER_INVENTORY='1',
               TOLLGATE_LAB_INVENTORY='/work/inventory.yaml', TOLLGATE_VENUE='labgrid',
               TOLLGATE_LABGRID_PLACE='empty', TOLLGATE_LABGRID_CLIENT='/runtime/bin/trial-client',
               TOLLGATE_LAB_TYPE='physical', TOLLGATE_SSH_HOST='127.0.0.1',
               LG_HOSTNAME='trial', LG_USERNAME='trial')
    os.environ.update(env)
    ctx['env'] = env
    Path('/work/coordinator').mkdir()
    profile = dict(name='trial', runner='pytest',
                   matrix=dict(tier='venue', lab='physical', scope='test'), env={},
                   pytest=dict(testpaths=['/source/tests/test_venue_lifecycle.py']))
    Path('/work/trial.yaml').write_text(yaml.safe_dump(profile))
    inventory = dict(switch=dict(host='127.0.0.1'), coordinator=dict(address=COORD),
                     routers={'empty': dict(poe_port='lan1', address='127.0.0.1', place='empty')})
    Path('/work/inventory.yaml').write_text(yaml.safe_dump(inventory))
    manifest = yaml.safe_load(Path('/source/config/fleets.example.yaml').read_text().replace('REPLACE_ME_', 'trial_'))
    manifest['fleets'] = manifest['fleets'][:1]
    fleet = manifest['fleets'][0]
    fleet['coordinator']['address'] = COORD
    fleet['allowed_places'] = ['empty']
    fleet['shared_conflict_ids'] = []
    fleet['host_controller']['endpoint'] = '127.0.0.1:8099'
    Path('/work/observe.yaml').write_text(yaml.safe_dump(manifest))
    fleet['permitted_actions'] = ['observe', 'acquire', 'release']
    Path('/work/authorized.yaml').write_text(yaml.safe_dump(manifest))
    authority = FleetAuthority('/work/authority.db')
    authority.reconcile()
    budget = dict(vcpus_total=1, ram_mib_total=128, disk_mib_total=16, inodes_total=1000)
    authority.register_host(HostSpec(**budget))
    authority.register_lane(LaneSpec(lane_id='lane', fleet_id='trial_FLEET_A',
                                     resource_id='empty', **budget))
    ctx['authority'] = authority
    log = open('/work/coordinator.log', 'w+')
    ctx['log'] = log
    coordinator = subprocess.Popen([PY, '-I', '-B', '/runtime/bin/labgrid-coordinator',
                                    '--listen', COORD], cwd='/work/coordinator', env=env,
                                   stdout=log, stderr=subprocess.STDOUT)
    # Registered before the first assertion: a failed preparation must still stop
    # the coordinator, or the next bounded service inherits a live port.
    ctx['coordinator'] = coordinator
    deadline = time.monotonic() + 10
    while True:
        try:
            with socket.create_connection(('127.0.0.1', 20408), timeout=.2):
                break
        except OSError:
            assert coordinator.poll() is None
            assert time.monotonic() < deadline
            time.sleep(.1)
    create = command([PY, '-I', '-B', '/runtime/bin/labgrid-client', '-x', COORD,
                      '-p', 'empty', 'create'], env)
    assert create.returncode == 0
    bench = LabgridBench(COORD, 'empty')
    assert bench.owner() == ''
    base = [PY, '-B', '/source/scripts/run-profile.py', '--profile', '/work/trial',
            '--no-render', '--fleet', 'trial_FLEET_A', '--resource', 'empty',
            '--lane', 'lane', '--authority-store', '/work/authority.db',
            '--demand-vcpus', '1', '--demand-ram-mib', '64', '--demand-disk-mib', '1',
            '--demand-inodes', '1']
    ctx['bench'] = bench
    ctx['base'] = base


def runner(base, *, manifest, run_dir, ttl_s, run_id, extra_env=None):
    env = dict(os.environ, TOLLGATE_RUN_ID=run_id)
    env.update(extra_env or {})
    return (base + ['--lease-ttl', str(ttl_s), '--fleet-manifest', manifest,
                    '--run-dir', run_dir] + RUNNER_EXTRA, env)


def release_records(run_dir):
    return [json.loads(p.read_text()) for p in Path(run_dir).rglob('release.json')]


def first_release(run_dir):
    records = release_records(run_dir)
    assert records, f'no release evidence under {run_dir}'
    return records[0]


# --------------------------------------------------------------------------- #
# scenarios
# --------------------------------------------------------------------------- #
def scenario_baseline(ctx):
    authority, bench, base, env = ctx['authority'], ctx['bench'], ctx['base'], ctx['env']
    extra = RUNNER_EXTRA
    denied_argv = base + ['--lease-ttl', '30', '--fleet-manifest', '/work/observe.yaml',
                          '--run-dir', '/work/denied'] + extra
    denied = command(denied_argv, env)
    assert denied.returncode == 75
    assert not Path('/work/child.json').exists()
    assert not authority.allocations()
    assert not Path('/work/denied').exists()
    print(json.dumps(dict(unauthorized='PASS', rc=75, child=False, allocations=0)), flush=True)
    argv, run_env = runner(base, manifest='/work/authorized.yaml', run_dir='/work/positive',
                           ttl_s=30, run_id=f'trial-{uuid.uuid4().hex[:8]}')
    positive = command(argv, run_env)
    states = [a.state.value for a in authority.allocations()]
    owner = bench.owner()
    print(json.dumps(dict(states=states, owner_after=owner)), flush=True)
    assert positive.returncode == 0
    child = json.loads(Path('/work/child.json').read_text())
    junit = ET.parse(next(Path('/work/positive').rglob('junit.xml')))
    cases = junit.findall('.//testcase')
    assert len(cases) == 1 and not junit.findall('.//failure') and not junit.findall('.//error') and not junit.findall('.//skipped')
    release = first_release('/work/positive')
    assert states == ['released'] and owner == ''
    print(json.dumps(dict(lifecycle='PASS', junit_pass_count=1, child=child, release=release)), flush=True)


def scenario_renew(ctx):
    """A child that outlives its TTL several times over stays leased."""
    authority, bench, base, env = ctx['authority'], ctx['bench'], ctx['base'], ctx['env']
    run_id = f'trial-{uuid.uuid4().hex[:8]}'
    argv, run_env = runner(base, manifest='/work/authorized.yaml', run_dir='/work/renew',
                           ttl_s=RENEW_TTL_S, run_id=run_id,
                           extra_env={'TOLLGATE_TRIAL_SLEEP_S': str(RENEW_CHILD_SLEEP_S)})
    record = run_watched(argv, run_env, authority=authority, run_id=run_id, timeout=120)
    release = first_release('/work/renew')
    release_mtime = next(Path('/work/renew').rglob('release.json')).stat().st_mtime
    child = json.loads(Path('/work/child.json').read_text())
    evidence = renewal_evidence(record['samples'], ttl_s=RENEW_TTL_S, exited_at=record['exited_at'])
    # Every sampled extension must have been observed BEFORE the release was
    # written: renewals that happen during teardown prove nothing.
    renew_bumps = [b['t'] for a, b in zip(record['samples'], record['samples'][1:])
                   if a.get('expires_at') is not None and b.get('expires_at') is not None
                   and b['expires_at'] > a['expires_at']]
    last_renewal = max(renew_bumps) if renew_bumps else 0.0
    print(json.dumps(dict(scenario='renew', run_id=run_id, rc=record['rc'],
                          renewals_observed=evidence['renewals_observed'],
                          samples=evidence['samples'], lapsed_samples=evidence['lapsed_samples'],
                          live_at_exit=evidence['live_at_exit'], child=child,
                          release=release, last_renewal_before_release=last_renewal < release_mtime,
                          exited_at=record['exited_at'], release_mtime=release_mtime)), flush=True)
    assert record['rc'] == 0, record['stderr'][-2000:]
    assert child['sleep_s'] == RENEW_CHILD_SLEEP_S and child['duration_s'] >= RENEW_CHILD_SLEEP_S
    # The child really did outlive the TTL it started with.
    assert evidence['would_have_expired_before_exit'], evidence
    assert evidence['renewals_observed'] >= 2, f"only {evidence['renewals_observed']} renewals"
    assert evidence['lapsed_samples'] == 0, evidence
    assert evidence['live_at_exit'], evidence
    assert last_renewal < release_mtime, 'a renewal landed after the lane was released'
    assert release['state'] == 'clean_released', release
    assert [a.state.value for a in authority.allocations()] == ['released']
    assert bench.owner() == ''
    print(json.dumps(dict(lifecycle='PASS', scenario='renew')), flush=True)


def _renewal_failure(ctx, *, mode):
    """Lose the lease mid-run; the child must die and the lane must stay held."""
    authority, bench, base, env = ctx['authority'], ctx['bench'], ctx['base'], ctx['env']
    run_id = f'trial-{uuid.uuid4().hex[:8]}'
    run_dir = f'/work/{mode}'
    argv, run_env = runner(base, manifest='/work/authorized.yaml', run_dir=run_dir,
                           ttl_s=FAIL_TTL_S, run_id=run_id,
                           extra_env={'TOLLGATE_TRIAL_SLEEP_S': str(FAIL_CHILD_SLEEP_S)})

    def enough(samples, latest):
        return latest.get('state') == 'active' and _renewals(samples) >= 1

    def revoke(_samples):
        if mode == 'renewal_expire':
            # `expire(now=<future>)` quarantines and advances the fence exactly
            # as the store's own TTL sweep would: the next beat must see a claim
            # that is no longer live.
            expired = authority.expire(now=time.time() + 3600)
            print(json.dumps(dict(scenario=mode, action='expire',
                                  run_id=run_id, expired=[a.run_id for a in expired])), flush=True)
        else:
            fence = authority.fence(run_id, note='trial: revoke mid-run')
            print(json.dumps(dict(scenario=mode, action='fence', run_id=run_id, fence=fence)), flush=True)

    record = run_watched(argv, run_env, authority=authority, run_id=run_id,
                         action=revoke, trigger=enough, timeout=120)
    # ``glob`` is a one-shot iterator: consume it once. Calling ``list()`` first
    # and ``next()`` second raises StopIteration on the consumed generator.
    logs = list(Path(run_dir).glob('raw/venue/output.log'))
    log_text = logs[0].read_text() if logs else ''
    release = first_release(run_dir)
    run_states = [a.state.value for a in authority.allocations()]
    evidence = dict(
        exit_code=record['rc'],
        release_state=release['state'],
        marker_seen=('"finished"' in log_text),
        duration_s=record['exited_at'] - record['started_at'],
        child_sleep_s=FAIL_CHILD_SLEEP_S,
        renewals_observed=_renewals(record['samples']),
    )
    verdict = fatal_renewal_verdict(**evidence)
    stragglers = surviving_run_processes(run_dir)
    print(json.dumps(dict(scenario=mode, run_id=run_id, action_fired=record['action_fired'],
                          **evidence, verdict=verdict, stragglers=stragglers,
                          release=release, run_states=run_states,
                          stderr=record['stderr'][-2000:])), flush=True)
    assert record['action_fired'], 'the mid-run revocation never fired'
    assert_fatal_renewal(verdict)
    assert record['rc'] == 77, record['stderr'][-2000:]
    assert not stragglers, f'runner descendants survived the loss: {stragglers}'
    assert release['state'] == 'cleanup_pending', release
    assert release['clean'] is False, release
    assert 'SUPERVISION FAILED' in record['stderr'], record['stderr'][-2000:]
    assert run_states and run_states[0] != 'released', run_states
    print(json.dumps(dict(lifecycle='PASS', scenario=mode)), flush=True)


def scenario_contention(ctx):
    """A second runner for the same place is refused before it can acquire."""
    authority, bench, base, env = ctx['authority'], ctx['bench'], ctx['base'], ctx['env']
    run_a = f'trial-{uuid.uuid4().hex[:8]}'
    run_b = f'trial-{uuid.uuid4().hex[:8]}'
    owner_a = ClientIdentity.for_run(run_a, host='trial').owner
    owner_b = ClientIdentity.for_run(run_b, host='trial').owner
    argv_a, env_a = runner(base, manifest='/work/authorized.yaml', run_dir='/work/contend-a',
                           ttl_s=FAIL_TTL_S, run_id=run_a,
                           extra_env={'TOLLGATE_TRIAL_SLEEP_S': '6'})
    argv_b, env_b = runner(base, manifest='/work/authorized.yaml', run_dir='/work/contend-b',
                           ttl_s=30, run_id=run_b)
    print(json.dumps(dict(argv=argv_a, watched=True, run_id=run_a)), flush=True)
    process = subprocess.Popen(argv_a, env=env_a, cwd='/work', text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    samples = []
    refused = None
    owner_before = None
    owner_after = None
    try:
        deadline = time.monotonic() + 60
        while process.poll() is None and time.monotonic() < deadline:
            latest = sample_lease(authority, run_a)
            samples.append(latest)
            # The precondition for contention is *ownership*, not renewal: A
            # must hold the coordinator place under its own identity AND have
            # the lane active in the store. Waiting for a renewal would make the
            # test depend on A's heartbeat cadence instead of on the claim.
            if latest.get('state') == 'active' and bench.owner() == owner_a:
                owner_before = bench.owner()
                refused = command(argv_b, env_b, timeout=65)
                # Read ownership IMMEDIATELY after B's refusal: B must not have
                # been able to free or take A's place. Reading it after A's own
                # release would make the check vacuous.
                owner_after = bench.owner()
                break
            time.sleep(0.05)
        first_running = process.poll() is None
        if not first_running:
            out, err = process.communicate(timeout=30)
            raise AssertionError(
                'the first runner exited before the second was refused: '
                f'rc={process.returncode} states={[s.get("state") for s in samples]} '
                f'stdout={out[-3000:]!r} stderr={err[-3000:]!r}')
        assert refused is not None, f'the second runner was never attempted; samples={samples[-5:]}'
        out, err = process.communicate(timeout=90)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    owner_final = bench.owner()
    verdict = contention_verdict(
        rc_b=refused.returncode, stderr_b=refused.stderr, owner_a=owner_a, owner_b=owner_b,
        owner_before=owner_before, owner_after=owner_after,
        owner_final=owner_final, run_dir_b_exists=Path('/work/contend-b').exists(),
        # B's own child record would live here if its fixture had ever run: the
        # fixture writes it right after acquiring, so B overwriting A's record
        # is itself proof that B reached the venue.
        child_b_exists=(Path('/work/child.json').exists()
                        and json.loads(Path('/work/child.json').read_text()).get('run_id') == run_b),
        allocations_for_b=any(a.run_id == run_b for a in authority.allocations()),
        first_still_running=first_running,
    )
    release_a = first_release('/work/contend-a')
    print(json.dumps(dict(scenario='contention', run_a=run_a, run_b=run_b,
                          owner_a=owner_a, owner_b=owner_b, owner_before=owner_before,
                          owner_after=owner_after, rc_b=refused.returncode,
                          stderr_b=refused.stderr, verdict=verdict,
                          rc_a=process.returncode, release_a=release_a,
                          stderr_a=err[-1000:])), flush=True)
    assert verdict['distinct_owners'] and verdict['refused_before_acquisition'], verdict
    assert first_running
    assert process.returncode == 0, err[-2000:]
    assert bench.owner() == '', 'the place was not free after the first runner released it'
    assert release_a['state'] == 'clean_released', release_a
    assert [a.state.value for a in authority.allocations()].count('released') == 1
    print(json.dumps(dict(lifecycle='PASS', scenario='contention')), flush=True)


def scenario_crash_run(ctx):
    """SIGKILL the runner mid-run; prove the lane is quarantined, never freed.

    A crashed or disconnected runner is the case no in-process supervision can
    handle: the supervisor itself is gone, so nothing terminates the venue child
    and nothing releases the lease. The only safety property left is that the
    store refuses to hand the lane out — first while the dead holder's lease is
    unexpired, then, after real expiry, only as ``quarantined``.

    Everything below is read from the real SQLite store and the real
    coordinator. The kill is a real ``SIGKILL`` to the runner's process group,
    exactly what a lost machine or a ``kill -9`` produces.
    """
    authority, bench, base, env = ctx['authority'], ctx['bench'], ctx['base'], ctx['env']
    run_a = f'trial-{uuid.uuid4().hex[:8]}'
    run_b = f'trial-{uuid.uuid4().hex[:8]}'
    run_dir = '/work/crash-a'
    argv_a, env_a = runner(base, manifest='/work/authorized.yaml', run_dir=run_dir,
                           ttl_s=CRASH_TTL_S, run_id=run_a,
                           extra_env={'TOLLGATE_TRIAL_SLEEP_S': str(CRASH_CHILD_SLEEP_S)})
    argv_b, env_b = runner(base, manifest='/work/authorized.yaml', run_dir='/work/crash-b',
                           ttl_s=30, run_id=run_b)
    owner_a = ClientIdentity.for_run(run_a, host='trial').owner

    # --- start the authorized run and wait until the child really holds it ---
    print(json.dumps(dict(argv=argv_a, watched=True, run_id=run_a,
                          scenario='crash_run', phase='start')), flush=True)
    process = subprocess.Popen(argv_a, env=env_a, cwd='/work', text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True)
    samples = []
    child_record = None
    before_kill = None
    killed_at = None
    try:
        deadline = time.monotonic() + 60
        while process.poll() is None and time.monotonic() < deadline:
            latest = sample_lease(authority, run_a)
            samples.append(latest)
            # The kill precondition is that the DUT is genuinely in use: the lane
            # is ACTIVE in the store AND the coordinator reports THIS run's owner
            # for the place. Deliberately NOT the child's own record: the venue
            # fixture writes ``child.json`` after its hold window (that is where
            # its duration is measured), so waiting for it would mean killing the
            # runner after the child had already finished and released — no crash
            # at all. Ownership is the live proof that a process is on the DUT.
            if latest.get('state') == 'active' and place_owner(bench) == owner_a:
                child_record = (json.loads(Path('/work/child.json').read_text())
                                if Path('/work/child.json').exists() else None)
                before_kill = latest
                killed_at = time.time()
                # SIGKILL the whole group: the runner, the pytest child and any
                # helper it spawned, with no chance to clean up.
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                print(json.dumps(dict(scenario='crash_run', action='SIGKILL',
                                      run_id=run_a, pgid=process.pid,
                                      at=killed_at, sample=latest,
                                      child=child_record)), flush=True)
                break
            time.sleep(0.2)
        if killed_at is None:
            out, err = ('', '')
            if process.poll() is not None:
                out, err = process.communicate(timeout=30)
            raise AssertionError(
                'the run never reached ACTIVE + owned + child-recorded, so there was '
                f'nothing to crash; rc={process.poll()} samples={samples[-5:]} '
                f'stdout={out[-3000:]!r} stderr={err[-3000:]!r}')
        out, err = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    runner_rc = process.returncode
    pre_kill = before_kill or {}
    print(json.dumps(dict(scenario='crash_run', phase='killed', run_id=run_a,
                          runner_rc=runner_rc, stdout=out[-1500:],
                          stderr=err[-1500:])), flush=True)

    # A SIGKILLed runner exits by signal, never by a supervision verdict.
    assert runner_rc == -signal.SIGKILL, (
        f'the runner did not die from SIGKILL (rc={runner_rc}); '
        'the crash precondition is unproven')
    # The supervisor is gone but its venue child is NOT: the child owns its own
    # session (``start_new_session``), so a whole-group SIGKILL of the runner
    # leaves a live process that may still be mutating the DUT. That survivor is
    # precisely why the store must not reassign the lane.
    stragglers = surviving_run_processes(run_dir)
    # ``child.json`` is written by the venue fixture AFTER its hold window, and
    # the "finished" marker after that. Neither may exist at a mid-hold kill:
    # their presence would mean the child had already completed and been able to
    # release the place, so the SIGKILL hit a finishing run, not a crashed one.
    venue_log = next(iter(Path(run_dir).glob('raw/venue/output.log')), None)
    venue_text = venue_log.read_text() if venue_log else ''
    mid_hold = (not Path('/work/child.json').exists()
                and '"finished"' not in venue_text
                and child_record is None)
    print(json.dumps(dict(scenario='crash_run', phase='orphans_after_kill',
                          run_dir=run_dir, survivors=stragglers,
                          child_record=child_record, mid_hold=mid_hold,
                          venue_log_tail=venue_text[-600:])), flush=True)
    assert mid_hold, (
        'the venue child had already completed before the kill, so the crash '
        f'proves nothing about a live DUT: child_record={child_record!r}')
    assert stragglers, (
        'no process survived the SIGKILL: the runner had already cleaned up, so '
        'this is not a crash/disconnect and proves nothing about a live DUT')

    # --- (a) while the dead holder's lease is unexpired: not reassignable ---
    # A real, authorized runner for the SAME lane is attempted immediately. It
    # must be refused before acquisition — the store still believes the dead
    # holder owns it, and belief is correct here: the child is still alive.
    holder_after_kill = sample_lease(authority, run_a)
    attempt = command(argv_b, env_b, timeout=65)
    reason_b_unexpired = refusal_reason(attempt.stderr)
    print(json.dumps(dict(scenario='crash_run', phase='reassign_while_unexpired',
                          run_b=run_b, rc=attempt.returncode,
                          reason=reason_b_unexpired, holder=holder_after_kill,
                          stderr=attempt.stderr[-1500:])), flush=True)
    # "Reassignable" is judged by the STORE, not by the runner's exit code: a
    # granted reservation leaves an allocation row even if the run then fails on
    # the coordinator. rc == 0 alone would miss that.
    b_allocations_early = [a for a in authority.allocations() if a.run_id == run_b]
    reassign_while_unexpired = bool(b_allocations_early) or attempt.returncode == 0
    assert not b_allocations_early and attempt.returncode != 0, (
        'a second runner acquired the lane while the dead holder\'s lease was '
        f'unexpired: rc={attempt.returncode} allocations={[a.run_id for a in b_allocations_early]} '
        f'stdout={attempt.stdout[-2000:]!r}')

    # --- (e) the dead runner's place ownership is still visible -------------
    owner_after_death = place_owner(bench)
    print(json.dumps(dict(scenario='crash_run', phase='owner_after_death',
                          owner=owner_after_death, expected=owner_a)), flush=True)

    # --- (b)/(c) real TTL expiry, caused by the second runner's own admission -
    # No injected clock and no explicit ``expire()`` call: the harness waits past
    # the lease the dead runner was given, and then a REAL authorized runner for
    # the same lane is started. Its own admission transaction is what sweeps the
    # expired claim — so the very transaction that could hand the lane out is the
    # one that must refuse. Two failures are covered by one observation: expiry
    # that frees the lane (b), and a refusal that is not durable (c).
    fence_before = int(holder_after_kill.get('fence') or pre_kill.get('fence') or 0)
    # The dead holder's last renewal set expiry to (last beat + ttl); nothing
    # renews it again, so this wait is exactly the store's own TTL elapsing.
    wait_s = max(0.0, float(holder_after_kill['expires_at']) - time.time()) + CRASH_EXPIRY_MARGIN_S
    print(json.dumps(dict(scenario='crash_run', phase='awaiting_real_expiry',
                          wait_s=round(wait_s, 2), ttl_s=CRASH_TTL_S)), flush=True)
    time.sleep(wait_s)
    # A plain read after the TTL must not have freed or reassigned anything:
    # there is no lazy read path that hands a lane over on a timestamp.
    holder_past_expiry_read = sample_lease(authority, run_a)
    print(json.dumps(dict(scenario='crash_run', phase='read_after_ttl',
                          holder=holder_past_expiry_read)), flush=True)

    attempt_b = command(argv_b, env_b, timeout=65)
    reason_b = refusal_reason(attempt_b.stderr)
    child_b = (Path('/work/child.json').exists()
               and json.loads(Path('/work/child.json').read_text()).get('run_id') == run_b)
    print(json.dumps(dict(scenario='crash_run', phase='refused_while_quarantined',
                          run_b=run_b, rc=attempt_b.returncode, reason=reason_b,
                          child_b=child_b, stderr=attempt_b.stderr[-2000:])), flush=True)

    holder_after_expiry = sample_lease(authority, run_a)
    lane = authority.headroom('lane')
    quarantine = authority.quarantined().get('lane')
    dead = authority.allocation(run_a)
    print(json.dumps(dict(scenario='crash_run', phase='after_expiry',
                          holder=holder_after_expiry,
                          store_state=dead.state.value, store_fence=dead.fence,
                          store_note=dead.note,
                          lane_quarantined=lane.quarantined,
                          quarantine_reason=lane.quarantine_reason,
                          quarantine=quarantine,
                          lane_allocations=list(lane.allocations),
                          free=dict(lane.free))), flush=True)

    # --- (d) the dead holder's token must authenticate nothing --------------
    # Replayed with the fence the runner actually held when it died, against the
    # real store. Any 'ACCEPTED' is a zombie that can still drive a DUT.
    stale = stale_transition_outcomes(authority, run_a, fence_before)
    print(json.dumps(dict(scenario='crash_run', phase='stale_transitions',
                          run_id=run_a, stale_fence=fence_before,
                          store_fence=dead.fence, store_state=dead.state.value,
                          outcomes=stale)), flush=True)

    # --- the surviving child is still alive, and still not the lane's owner --
    survivors = surviving_run_processes(run_dir)
    print(json.dumps(dict(scenario='crash_run', phase='survivors_at_end',
                          survivors=survivors)), flush=True)

    # --- assemble and gate ---------------------------------------------------
    releases = [r for r in release_records(run_dir) if r.get('run_id') in (run_a,)]
    release_clean = any(r.get('state') == 'clean_released' or r.get('clean') is True
                        for r in releases)
    allocations_for_a = [a for a in authority.allocations() if a.run_id == run_a]
    events = [e for e in authority.snapshot()['events'] if e['run_id'] == run_a]
    print(json.dumps(dict(scenario='crash_run', phase='store_events', run_a=run_a,
                          events=events)), flush=True)
    expiry_events = sum(1 for e in events if e['kind'] == 'lease_expired')
    released_events = sum(1 for e in events if e['kind'] == 'released')
    verdict = crash_release_verdict(
        state_before_kill=pre_kill.get('state'),
        expires_at_before_kill=pre_kill.get('expires_at'),
        killed_at=killed_at,
        state_after_expiry=dead.state.value,
        lane_quarantined=lane.quarantined,
        quarantine_reason=lane.quarantine_reason,
        reassign_while_unexpired=reassign_while_unexpired,
        reassign_reason=reason_b_unexpired,
        second_refusal_reason=reason_b,
        second_rc=attempt_b.returncode,
        second_child_exists=child_b,
        second_allocation_exists=any(a.run_id == run_b for a in authority.allocations()),
        stale_refusals=stale,
        fence_before=fence_before,
        fence_after=dead.fence,
        owner_after_death=owner_after_death,
        owner_a=owner_a,
        release_recorded=release_clean,
        allocations_for_a=bool(allocations_for_a),
        expiry_events=expiry_events,
        released_events=released_events,
    )
    print(json.dumps(dict(scenario='crash_run', run_a=run_a, run_b=run_b,
                          runner_rc=runner_rc, owner_a=owner_a,
                          child=child_record, verdict=verdict,
                          release_records=releases,
                          store=dict(state=dead.state.value, fence=dead.fence,
                                     note=dead.note),
                          run_states=[a.state.value for a in authority.allocations()])),
          flush=True)
    assert_crash_release(verdict)
    # The safety-critical two, restated as hard requirements on the same facts.
    assert dead.state is State.QUARANTINED, dead.state
    assert all(v != 'ACCEPTED' for v in stale.values()), stale
    print(json.dumps(dict(lifecycle='PASS', scenario='crash_run')), flush=True)


def main():
    scenario = os.environ.get('TOLLGATE_TRIAL_SCENARIO', 'baseline').strip() or 'baseline'
    print(json.dumps(dict(scenario=scenario, request='start')), flush=True)
    ctx = {}
    try:
        prepare(ctx)
        if scenario == 'baseline':
            scenario_baseline(ctx)
        elif scenario == 'renew':
            scenario_renew(ctx)
        elif scenario in ('renewal_fail', 'renewal_expire'):
            _renewal_failure(ctx, mode=scenario)
        elif scenario == 'contention':
            scenario_contention(ctx)
        elif scenario == 'crash_run':
            scenario_crash_run(ctx)
        else:
            raise SystemExit(f'unknown TOLLGATE_TRIAL_SCENARIO {scenario!r}')
    finally:
        # Teardown runs even when ``prepare()`` failed half-way: a leftover
        # coordinator or open log would poison the next bounded service.
        coordinator = ctx.get('coordinator')
        if coordinator is not None:
            coordinator.terminate()
            coordinator.wait(timeout=5)
        log = ctx.get('log')
        if log is not None:
            log.seek(0)
            print(json.dumps(dict(coordinator_log=log.read())), flush=True)
            log.close()


if __name__ == '__main__':
    main()
