"""Offline tests for the two prerequisites a clean run needs before it can pass.

Both were found by running `run-lab.sh up` from a fresh lab dir, and both fail
in a way that reads like a product bug rather than a missing prerequisite:

1. No apk in a fresh dir  -> "no artifact matching tollgate-wrt_*_x86_64.apk"
2. No reachable resolver  -> `apk add` dies with the misleading
                             "Failed to send request: Operation not permitted"

These tests pin the mechanism of each fix so the rail cannot silently lose it.
They need no QEMU, no KVM, no network.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LAB = REPO_ROOT / "scripts" / "lab"
FETCH = LAB / "fetch-apk.sh"
DNS = LAB / "lab-dns.sh"
FETCH_CDK = LAB / "fetch-cdk-cli.sh"
RUN_LAB = LAB / "run-lab.sh"
VIRTUAL_LAB = REPO_ROOT / "scripts" / "virtual-lab.py"

pytestmark = [pytest.mark.api, pytest.mark.virtual_lab]


def _read(path: Path) -> str:
    assert path.is_file(), f"missing required file: {path}"
    return path.read_text()


@pytest.mark.parametrize("script", [FETCH, DNS, FETCH_CDK, RUN_LAB, LAB / "start-fake-mint.sh"])
def test_scripts_are_valid_bash(script):
    r = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert r.returncode == 0, f"bash -n failed for {script.name}:\n{r.stderr}"


# ── 1. the apk fetch step closes the fresh-dir chicken-and-egg ───────────────

def test_up_fetches_an_apk_before_installing():
    """`up` must fetch when artifacts/ is empty, or a clean dir dead-ends."""
    body = _read(RUN_LAB)
    assert "fetch-apk.sh" in body, "up does not call the apk fetch step"
    fetch_at = body.index('"$REPO_ROOT/scripts/lab/fetch-apk.sh"')
    # Match the INVOCATION, not the mention in the comment above it.
    install_at = body.index('install-tollgate --host localhost')
    assert fetch_at < install_at, "the apk must be fetched BEFORE install-tollgate runs"
    # Idempotence lives in fetch-apk.sh (it exits early when an apk is staged),
    # so `up` can always call it without re-downloading.
    assert "already staged" in _read(FETCH), (
        "fetch-apk.sh must be idempotent so repeated `up` runs do not re-download"
    )


def test_fetch_apk_verifies_the_checksum_before_staging():
    """Installing bytes that differ from the published SHA256SUMS makes every
    later 'it works' claim unfounded."""
    body = _read(FETCH)
    assert "SHA256SUMS" in body
    assert "sha256sum" in body
    assert re.search(r'\[ "\$expected" = "\$actual" \]', body), (
        "fetch-apk.sh must compare expected vs actual digest and refuse on mismatch"
    )
    assert re.search(r"refusing to install unverified bytes", body), (
        "a release without SHA256SUMS must be refused, not trusted"
    )


def test_fetch_apk_matches_arch_not_a_fixed_suffix():
    """Published asset names changed shape between releases
    (…_alpha4_pre21_x86_64.apk vs …_x86_64-x86-64.apk); matching a fixed suffix
    broke the fetch."""
    body = _read(FETCH)
    assert "x86_64" in body
    assert re.search(r"tollgate-wrt_\[\^\"\]\*x86_64", body), (
        "fetch-apk.sh should match the arch token anywhere in the name"
    )


# ── 2. the resolver prerequisite ────────────────────────────────────────────

def test_provisioner_points_guest_dns_at_the_lab_forwarder():
    """The provisioned LAN DNS must be the host bridge, not 8.8.8.8.

    Measured: 8.8.8.8 is unreachable from the guest on this host, so dnsmasq
    has no upstream and apk dies with 'Operation not permitted'. A regression
    here is silent — the lab boots, verify passes, and only `apk add` fails.
    """
    body = _read(VIRTUAL_LAB)
    assert "uci set network.lan.dns='__HOST_BRIDGE_IP__'" in body, (
        "provisioning must set LAN DNS to the lab forwarder on the host bridge"
    )
    assert "uci set network.lan.dns='8.8.8.8'" not in body, (
        "the unreachable public resolver must not come back"
    )


def test_up_starts_the_resolver_before_installing():
    """The resolver must be up before `apk add` runs, and AFTER start-poc
    creates the bridge it binds to (measured: starting it first failed with
    'bridge tg-poc-br is absent')."""
    body = _read(RUN_LAB)
    boot_at = body.index('start-poc --host localhost')
    dns_at = body.index('"$REPO_ROOT/scripts/lab/lab-dns.sh" start')
    install_at = body.index('install-tollgate --host localhost')
    assert boot_at < dns_at, (
        "the resolver binds to the lab bridge, which start-poc creates — "
        "it cannot start before the VM"
    )
    assert dns_at < install_at, (
        "the resolver must be up before install-tollgate, or the guest's "
        "first apk call has no DNS"
    )


def test_lab_dns_adopts_an_orphan_listener():
    """A hand-run dnsmasq can hold :53 with its pidfile deleted. Binding then
    fails with a bare 'Address already in use' and the lab cannot start."""
    body = _read(DNS)
    assert "listener_pid" in body, "lab-dns.sh must locate the socket owner"
    assert "adopted existing forwarder" in body, (
        "lab-dns.sh must adopt a healthy orphan instead of failing to bind"
    )


def test_lab_dns_status_works_unprivileged():
    """dnsmasq binds :53 as root, so `kill -0` from the session that started it
    returns EPERM; treating that as 'dead' reported a live forwarder as down."""
    body = _read(DNS)
    assert re.search(r"sudo -n kill -0", body), (
        "liveness must fall back to a privileged kill -0 (EPERM != dead)"
    )


# ── 3. the mint must be running and the module repointed BEFORE verify ──────

def test_up_starts_mint_and_repoints_module_before_verify():
    """A freshly installed module advertises production mints (or none), which
    is DEGRADED mode: no price_per_step tags, and verify refuses it. Doing this
    after verify makes a clean run fail at the last gate for the wrong reason."""
    body = _read(RUN_LAB)
    verify_at = body.index("verify-lab")
    assert body.index("start-fake-mint.sh") < verify_at, (
        "the mint must be started before verify"
    )
    assert body.index("configure-fake-mint.py") < verify_at, (
        "the module must be repointed at the lab mint before verify"
    )


def test_configure_fake_mint_clears_seeded_wallet_state():
    """The install seeds wallet.db with production keysets. On a host without
    reachable internet the startup refresh never completes, so the module
    wedges in 'starting' and :2121 answers 503 forever."""
    body = _read(LAB / "configure-fake-mint.py")
    assert "wallet-quarantine" in body, (
        "configure-fake-mint.py must quarantine the seeded wallet.db"
    )
    assert "wallet.db" in body and "ecash" in body, (
        "both wallet.db and the ecash/ dir must be quarantined"
    )
    assert "starting" in body, "the reason (permanent 503 'starting') must be documented"


def test_start_fake_mint_keeps_state_inside_the_lab_dir():
    """This host's /tmp is tmpfs: a mint db there is RAM pressure and is lost."""
    body = _read(LAB / "start-fake-mint.sh")
    assert "/tmp/cdk-mintd-local" not in body, (
        "the mint work dir must not live in /tmp"
    )
    assert "CDK_MINTD_WORK:-$LAB/mint" in body, (
        "the mint work dir must default inside the lab dir"
    )


def test_start_fake_mint_checks_the_advertised_url():
    """A mint bound only to loopback passes an in-host probe and is unreachable
    from the guest, so every quote fails. The check must target the lab address
    the router is told to use, not 127.0.0.1."""
    body = _read(LAB / "start-fake-mint.sh")
    assert "reachable on loopback but not at" in body, (
        "the mint must be probed at the address the router will use"
    )
    assert 'http://${HOST_ADDR}:${PORT}/v1/keys' in body, (
        "the guest-facing probe must hit the lab address"
    )


# ── 4. the Cashu client prerequisite (the purchase needs a minter) ───────────
#
# Measured dead end this replaces: the docs told the operator to `curl` cdk-cli
# into /usr/local/bin and called it "checksum-verified" — the command verified
# nothing, and nothing checked that the binary was even a real cdk-cli. Both
# properties are now pinned here, because an unverified minter makes every
# "the purchase passed" claim unfounded.

def test_purchase_stages_a_verified_cdk_cli_itself():
    """`purchase` must resolve its minter instead of exiting 11 on a host that
    has cdk-mintd but no client — the gap that stopped the previous run."""
    body = _read(RUN_LAB)
    assert "fetch-cdk-cli.sh" in body, (
        "run-lab.sh purchase must stage cdk-cli when LAB_CDK_CLI is unset"
    )
    purchase_at = body.index("cmd_purchase()")
    assert "fetch-cdk-cli.sh" in body[purchase_at:], (
        "the fetch must happen inside cmd_purchase, not only at the top of the file"
    )
    assert "LAB_CDK_CLI=" in body, "the resolved path must be passed to purchase-e2e.py"


def test_fetch_cdk_cli_verifies_against_published_checksums():
    """The download must be refused unless it matches cdk's SHA256SUMS.

    cdk publishes a SHA256SUMS asset per release; comparing against it is the
    only thing that makes the binary trustworthy. A missing checksum line must
    be a hard failure, not a warning.
    """
    body = _read(FETCH_CDK)
    assert "SHA256SUMS" in body, "fetch-cdk-cli.sh must consult the published sums"
    assert re.search(r'\[ "\$expected" = "\$actual" \]', body), (
        "fetch-cdk-cli.sh must compare expected vs actual digest"
    )
    assert re.search(r"no checksum line for", body), (
        "a missing checksum line must be refused, not ignored"
    )
    assert re.search(r"checksum mismatch", body), (
        "a mismatch must abort instead of installing unknown bytes"
    )


def test_fetch_cdk_cli_records_the_digest_it_verified():
    """Without a recorded digest a re-run silently trusts whatever is on disk:
    the binary could be swapped between runs and nothing would notice."""
    body = _read(FETCH_CDK)
    assert re.search(r"\.sha256", body), (
        "fetch-cdk-cli.sh must persist the verified digest next to the binary"
    )
    assert "already staged and verified" in body, (
        "a re-run must re-verify the staged binary rather than assume it is good"
    )


def test_docs_do_not_claim_unverified_installation():
    """The README must not point operators at a bare `curl -o /usr/local/bin`.

    That instruction existed and was labelled 'checksum-verified' while doing no
    verification at all — a documentation defect that makes the rail's evidence
    unfounded. Pin the corrected path so it cannot come back.
    """
    body = _read(REPO_ROOT / "docs" / "VIRTUAL-LAB.md")
    assert "fetch-cdk-cli.sh" in body, (
        "the docs must point at the verifying fetch script"
    )
    assert "curl -fsSL -o /usr/local/bin/cdk-cli" not in body, (
        "the unverified install instruction must not return"
    )


# ── 5. the negative control must FAIL LOUDLY, not time out ──────────────────

def test_smoke_hard_timeout_exceeds_the_readiness_deadline():
    """A negative control must report WHY it failed, not die by pytest-timeout.

    Measured: `run-lab.sh smoke` passed `--timeout=180` while the fixture waited
    up to `TOLLGATE_LAB_READY_TIMEOUT=180`. Pointed at a dead address, the run
    aborted at exactly the deadline with a generic "Timeout" stack trace inside
    `api_up` — indistinguishable from a hung lab, and it denied the operator the
    fixture's actionable message. The hard timeout must be strictly larger.
    """
    body = _read(RUN_LAB)
    smoke = body[body.index("cmd_smoke()"):body.index("cmd_purchase()")]
    assert "hard=$(( ready + 60 ))" in smoke, (
        "the pytest hard timeout must be derived from, and larger than, the "
        "readiness deadline"
    )
    assert "--timeout=180" not in smoke, (
        "a fixed hard timeout equal to the readiness deadline reintroduces the bug"
    )
    assert '--timeout="$hard"' in smoke, "the derived hard timeout must be used"


# ── 6. teardown must not kill a process another lab dir is using ────────────

def test_teardown_spares_an_adopted_mint():
    """`down` must not kill a mint that this lab dir only ADOPTED.

    Measured on a clean-dir run: :8383 was already owned by
    `~/cdk-mint/cdk-mintd --work-dir ~/meshlink-lab/mint` — a different lab's
    mint. start-fake-mint.sh adopted it (correctly, so the run could proceed),
    but `down` then killed whatever pid was in the pidfile, which would take out
    someone else's running lab. The distinction must be recorded at adopt time
    and honoured at teardown.
    """
    mint = _read(LAB / "start-fake-mint.sh")
    assert "cdk-mintd.adopted" in mint, (
        "start-fake-mint.sh must record that the mint it adopted is not its own"
    )
    body = _read(RUN_LAB)
    down = body[body.index("cmd_down()"):body.index("cmd_status()")]
    assert "cdk-mintd.adopted" in down, (
        "cmd_down must consult the adopted marker before killing the mint"
    )
    assert "leaving adopted mint" in down, (
        "an adopted mint must be left running, and said so out loud"
    )


def test_teardown_spares_an_adopted_dns_forwarder():
    """Same class for the resolver: lab-dns.sh adopts an orphan holding :53 on
    the shared lab bridge, and must not kill it on stop."""
    dns = _read(DNS)
    assert "lab-dns.adopted" in dns, (
        "lab-dns.sh must record that an adopted forwarder is not its own"
    )
    stop = dns[dns.index("cmd_stop()"):]
    assert "leaving adopted forwarder running" in stop, (
        "stop must not kill a forwarder it merely adopted"
    )
