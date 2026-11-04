"""The runner's claim and the child fixture's identity must be ONE string.

Third lifecycle defect, measured on a real coordinator at trial
``prta-lifecycle-actual-v3``: the runner attributed the claim with
``ClientIdentity.current(env)`` while the fixture derived its own owner from
``ClientIdentity.for_run(run_id)``. In an environment that presets
``LG_USERNAME`` (every trial preset does) those are two different strings —
``trial/trial`` vs ``trial/prta-<run>-<digest>`` — so the positive case could
never assert ownership equality.

The fix is a single rule: **the runner exports the identity it exported the
run id for**. ``claim_env()`` carries ``LG_HOSTNAME``/``LG_USERNAME`` from
``ClientIdentity.for_run(run_id)``, so the child's ``ClientIdentity.current()``
(which reads exactly those two variables) IS the per-run identity, and the
claim owner, the child's acquisition and the fixture's assertion agree by
construction.

Written RED first against ``scripts/run-profile.py`` at ``5115f36``, where
``claim_env()`` exported no ``LG_*`` at all.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.fleet_binding import Binding, PlaceClaim
from lib.labgrid_venue import ClientIdentity

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("identity_runner", ROOT / "scripts/run-profile.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

PLACE = "place-a1"
DUT = "10.0.0.11"
COORDINATOR = "coord.example:20408"
RUN = "20261104T020000Z-trial-5115f36"
OTHER_RUN = "20261104T020001Z-trial-5115f36"


def _binding(owner: str) -> Binding:
    """A bound lane with no store behind it: ``claim_env`` reads only fields."""
    return Binding(
        fleet_id="test_FLEET_A",
        resource_id=PLACE,
        lane_id="lane-a",
        coordinator=COORDINATOR,
        place=PLACE,
        dut_address=DUT,
        owner=owner,
        origin="explicit-place",
        mode="bound",
    )


#: The environment a trial harness hands the runner: ``LG_HOSTNAME`` is real,
#: ``LG_USERNAME`` is a PRESET that is identical for every run.
TRIAL_ENV = {"LG_HOSTNAME": "trial", "LG_USERNAME": "trial", "PATH": "/usr/bin:/bin"}


def _plan(run_id: str = RUN, *, env: dict[str, str] | None = None) -> "runner.FleetPlan":
    """A plan built the way ``plan_fleet_run`` builds one.

    Same two steps: derive the run identity from the run id, then bind with
    ``owner = identity.owner``. Constructing the binding owner any other way
    would be testing a plan the runner never makes.
    """
    env = dict(TRIAL_ENV if env is None else env)
    identity = runner.run_identity(run_id, env)
    return runner.FleetPlan(
        admission=SimpleNamespace(),
        binding=_binding(identity.owner),
        run_id=run_id,
        ttl_s=30.0,
        identity=identity,
    )


# --------------------------------------------------------------------------- #
# 1. the claim carries the per-run identity, as environment the child reads
# --------------------------------------------------------------------------- #
def test_claim_env_exports_the_per_run_identity():
    """``LG_*`` in the claim MUST be exactly ``for_run(run_id).env()``.

    ``tests/conftest.py`` computes ``ClientIdentity.current()`` from the
    ambient environment, and ``ClientIdentity.current`` reads only these two
    variables. Unless the runner exports them, the child falls back to the OS
    user (or the trial preset) and the owners disagree.
    """
    plan = _plan()
    claim = plan.claim_env()

    expected = ClientIdentity.for_run(RUN, host="trial")
    assert claim["LG_HOSTNAME"] == expected.host
    assert claim["LG_USERNAME"] == expected.user
    assert claim[runner.ENV_OWNER] == expected.owner


def test_the_child_identity_derived_from_the_claim_is_the_claim_owner():
    """The exact expression ``conftest`` uses agrees with the exported owner.

    This is the agreement the v3 trial could not assert: build the child's
    environment by layering the claim over an environment that presets a
    DIFFERENT ``LG_USERNAME`` (as ``tools/fleet_trial.py`` does), then ask
    ``ClientIdentity.current`` what the fixture would acquire as.
    """
    plan = _plan()
    child = {**TRIAL_ENV, **plan.claim_env()}

    child_owner = ClientIdentity.current(child).owner
    assert child_owner == child[runner.ENV_OWNER]
    assert child_owner == ClientIdentity.for_run(RUN, host="trial").owner


def test_ambient_username_cannot_override_the_per_run_identity():
    """A preset ``LG_USERNAME`` in the parent must not survive into the owner.

    ``tools/fleet_trial.py`` presets ``LG_USERNAME=trial`` for every invocation.
    If that value reached the child, both runs of a trial would acquire (and
    release!) as the same coordinator owner — the collision ``for_run`` exists
    to prevent.
    """
    for ambient_user in ("trial", "runner", "root"):
        ambient = {**TRIAL_ENV, "LG_USERNAME": ambient_user}
        plan = _plan(env=ambient)
        child = {**ambient, **plan.claim_env()}
        assert child["LG_USERNAME"] != ambient_user
        assert ClientIdentity.current(child).owner == plan.binding.owner


def test_two_run_ids_never_share_an_owner_through_the_claim():
    """Per-run scoping survives the environment export."""
    a = _plan(RUN).claim_env()
    b = _plan(OTHER_RUN).claim_env()
    assert a[runner.ENV_OWNER] != b[runner.ENV_OWNER]
    assert a["LG_USERNAME"] != b["LG_USERNAME"]
    # ... and each child still resolves to its own claim.
    assert ClientIdentity.current({**a}).owner == a[runner.ENV_OWNER]
    assert ClientIdentity.current({**b}).owner == b[runner.ENV_OWNER]


def test_a_same_run_retry_reconciles_to_one_owner():
    """Retrying a run id is the same owner, not a second claim on the place."""
    assert _plan(RUN).claim_env()[runner.ENV_OWNER] == _plan(RUN).claim_env()[runner.ENV_OWNER]


def test_the_claim_still_forces_the_venue_and_attributes_the_run():
    """The identity fix must not disturb the place/venue/run-id triple."""
    claim = _plan().claim_env()
    assert claim["TOLLGATE_VENUE"] == "labgrid"
    assert claim["TOLLGATE_LABGRID_PLACE"] == PLACE
    assert claim["TOLLGATE_SSH_HOST"] == DUT
    assert claim[runner.ENV_PLACE] == PLACE
    assert claim[runner.ENV_RUN_ID] == RUN


# --------------------------------------------------------------------------- #
# 2. handoff: the fixture's decision, run with the child's environment
# --------------------------------------------------------------------------- #
def test_evaluate_handoff_agrees_when_the_child_env_is_used():
    """The runner's pre-flight handoff check must match the child's own.

    ``verify_claim_handoff`` runs ``evaluate_handoff`` before the reservation is
    spent; ``_claim_handoff_guard`` runs it again inside pytest. Both must
    reach ``CLAIM_AGREES`` for a bound run, and both must be fed the identity
    the child will acquire as.
    """
    from lib.claim_handoff import ClaimCode, evaluate_handoff

    plan = _plan()
    child = {**TRIAL_ENV, **plan.claim_env()}

    decision = evaluate_handoff(
        environ=child,
        resolved_place=PLACE,
        owns_place=True,
        identity_owner=ClientIdentity.current(child).owner,
    )
    assert decision.code is ClaimCode.CLAIM_AGREES, decision.message


def test_evaluate_handoff_agrees_via_the_runners_own_preflight(monkeypatch):
    """``verify_claim_handoff`` must not refuse a claim the child would honour."""
    from lib.claim_handoff import ClaimCode, evaluate_handoff

    seen: dict[str, object] = {}
    real = runner.evaluate_handoff

    def spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(runner, "evaluate_handoff", spy)

    plan = _plan()
    child = {**TRIAL_ENV, **plan.claim_env()}
    runner.verify_claim_handoff(plan.binding, run_id=RUN, env=child)

    assert seen, "verify_claim_handoff did not consult evaluate_handoff"
    identity = ClientIdentity.current(child).owner
    assert seen["identity_owner"] == identity
    assert identity == plan.binding.owner
    decision = real(environ={**child, **{runner.ENV_PLACE: PLACE}}, resolved_place=PLACE,
                    identity_owner=identity)
    assert decision.code is ClaimCode.CLAIM_AGREES, decision.message


def test_a_foreign_owner_is_still_refused():
    """The agreement must not have been bought by disabling the check."""
    from lib.claim_handoff import evaluate_handoff

    plan = _plan()
    child = {**plan.claim_env()}
    decision = evaluate_handoff(
        environ=child,
        resolved_place=PLACE,
        identity_owner="trial/someone-else",
    )
    assert decision.refused


def test_a_plan_whose_binding_names_another_run_is_refused():
    """The agreement is checked at the handoff, not assumed from construction.

    A binding attributed to a different run than the plan's own identity is
    exactly the failure the v3 trial shipped: the reservation and the
    acquisition name two owners. ``verify_claim_handoff`` must refuse it rather
    than export a claim the fixture will reject mid-run.
    """
    from lib.run_admission import AdmissionRefused

    env = dict(TRIAL_ENV)
    stale = runner.FleetPlan(
        admission=SimpleNamespace(),
        binding=_binding(ClientIdentity.for_run(OTHER_RUN, host="trial").owner),
        run_id=RUN,
        ttl_s=30.0,
        identity=runner.run_identity(RUN, env),
    )
    with pytest.raises(AdmissionRefused) as excinfo:
        runner.verify_claim_handoff(stale.binding, run_id=RUN, env=env)
    assert excinfo.value.reason == "claim_claim_owner_mismatch"


def test_an_identity_less_plan_still_exports_a_consistent_owner():
    """Defence in depth: no identity on the plan must not mean "ambient user".

    ``claim_env`` falls back to ``run_identity(run_id, {})`` — the real
    hostname, a per-run user — so the exported ``LG_*`` and the exported owner
    describe ONE identity even for a plan built without one. It still refuses
    at the handoff if that disagrees with the binding, which is fail-closed.
    """
    identity = runner.run_identity(RUN, {})
    plan = runner.FleetPlan(
        admission=SimpleNamespace(),
        binding=_binding(identity.owner),
        run_id=RUN,
        ttl_s=30.0,
    )
    claim = plan.claim_env()
    assert ClientIdentity.current(claim).owner == claim[runner.ENV_OWNER]
    assert claim["LG_USERNAME"].startswith("prta-")
