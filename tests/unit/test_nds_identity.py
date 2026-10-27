"""Unit tests for lib/nds_identity.py — NDS dependency identity (no router).

The point of the module is attribution: a green NDS run must name the exact
nodogsplash package revision and the binary hashes it exercised, at deploy time
AND before teardown, and must fail when it cannot.  These tests run entirely
against the recorded fixtures in tests/fixtures/nds_identity/records.json.

The three NEGATIVE CONTROLS live in ``TestNegativeControls`` and are the ones
that matter for the release gate — each plants one specific failure (missing
version / version mismatch / mock mode) and asserts the run fails *for that
reason*, not merely that it fails.
"""

from __future__ import annotations

import json
import os

import pytest

from lib import nds_identity as ni

pytestmark = [pytest.mark.story("P10")]

FIXTURE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "fixtures", "nds_identity", "records.json"
)


@pytest.fixture(scope="module")
def records():
    with open(FIXTURE_PATH) as handle:
        return json.load(handle)


def ssh_from(responses: dict, *, default: str = ""):
    """A run_ssh over recorded command→stdout pairs.

    An unknown command returns ``default`` (like a shell with no output) unless
    the test opts into strict mode by passing ``default=_UNRECORDED``.
    """
    def run_ssh(cmd: str) -> str:
        if cmd in responses:
            return responses[cmd]
        if default is _UNRECORDED:
            raise AssertionError(f"unrecorded probe command: {cmd!r}")
        return default
    return run_ssh


_UNRECORDED = object()


def capture_from(records, flavor="apk", *, phase="deploy", overrides=None, env=None, strict=True):
    responses = dict(records[flavor]["responses"])
    responses.update(overrides or {})
    return ni.capture(
        ssh_from(responses, default=_UNRECORDED if strict else ""),
        phase=phase,
        env=env or {},
        clock=lambda: "2026-10-27T02:30:00Z",
    )


def qualified_block(records, *, flavor="apk", overrides=None, qualification=None):
    before = capture_from(records, flavor, phase="deploy", overrides=overrides)
    after = capture_from(records, flavor, phase="teardown", overrides=overrides)
    return ni.build_dependency_block(
        before,
        after,
        qualification=qualification if qualification is not None else records["qualification"],
        release_evidence=True,
        env={},
    )


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


class TestParsePackageVersion:
    def test_apk_keeps_packaging_revision(self):
        parsed = ni.parse_package_version("nodogsplash-5.0.2-r2\n", "apk")
        assert parsed["version"] == "5.0.2"
        assert parsed["revision"] == "r2"
        assert parsed["full"] == "5.0.2-r2"

    def test_opkg_keeps_packaging_revision(self):
        parsed = ni.parse_package_version("nodogsplash - 5.0.2-r1\n", "opkg")
        assert parsed["version"] == "5.0.2"
        assert parsed["revision"] == "r1"
        assert parsed["full"] == "5.0.2-r1"

    def test_missing_revision_is_not_silently_filled(self):
        parsed = ni.parse_package_version("nodogsplash-5.0.2", "apk")
        assert parsed["version"] == "5.0.2"
        assert parsed["revision"] == ""
        assert parsed["full"] == "5.0.2"

    def test_empty_output_yields_empty_identity(self):
        assert ni.parse_package_version("", "apk")["full"] == ""
        assert ni.parse_package_version("\n", "opkg")["full"] == ""

    def test_a_wrong_package_name_is_not_matched(self):
        assert ni.parse_package_version("nodogsplash-hotspot-5.0.2-r2", "apk")["full"] == ""

    def test_snapshot_suffixed_version_survives(self):
        parsed = ni.parse_package_version("nodogsplash-5.0.2-r2.1", "apk")
        assert parsed["full"] == "5.0.2-r2.1"


class TestParseDaemonVersion:
    def test_reads_the_version_line_from_ndsctl_status(self, records):
        text = records["apk"]["responses"]["ndsctl status 2>/dev/null"]
        assert ni.parse_daemon_version(text) == "5.0.2"

    def test_missing_version_line_is_empty_not_guessed(self):
        assert ni.parse_daemon_version("NoDogSplash Status\nUptime: 1d\n") == ""

    def test_stable_status_drops_only_volatile_lines(self, records):
        text = records["apk"]["responses"]["ndsctl status 2>/dev/null"]
        stable = ni.stable_status_text(text)
        assert "Version: 5.0.2" in stable
        assert "Uptime:" not in stable
        assert "Total download:" not in stable
        assert "Total upload:" not in stable
        assert "Client authentications since start:" not in stable

    def test_volatile_lines_do_not_change_the_digest(self, records):
        text = records["apk"]["responses"]["ndsctl status 2>/dev/null"]
        moved = (
            text.replace("Uptime: 0d 0h 12m 31s", "Uptime: 0d 3h 44m 9s")
            .replace("Total download: 482 kByte; avg: 5.12 kbit/s",
                     "Total download: 99012 kByte; avg: 61.1 kbit/s")
            .replace("Client authentications since start: 3",
                     "Client authentications since start: 40")
        )
        assert ni.digest_text(ni.stable_status_text(text)) == ni.digest_text(
            ni.stable_status_text(moved)
        )

    def test_a_real_change_does_change_the_digest(self, records):
        text = records["apk"]["responses"]["ndsctl status 2>/dev/null"]
        moved = text.replace("Gateway Name: TollGate", "Gateway Name: SomebodyElse")
        assert ni.digest_text(ni.stable_status_text(text)) != ni.digest_text(
            ni.stable_status_text(moved)
        )


class TestParseHelpers:
    def test_parse_sha256sums_maps_paths(self):
        parsed = ni.parse_sha256sums(
            "a" * 64 + "  /usr/bin/nodogsplash\n"
            + "b" * 64 + " */usr/bin/ndsctl\n"
            "garbage\n"
        )
        assert parsed == {"/usr/bin/nodogsplash": "a" * 64, "/usr/bin/ndsctl": "b" * 64}

    def test_parse_openwrt_release(self):
        parsed = ni.parse_openwrt_release("OpenWrt|25.12.5|mediatek/filogic|aarch64_cortex-a53|OpenWrt 25.12.5 r1")
        assert parsed["id"] == "OpenWrt"
        assert parsed["release"] == "25.12.5"
        assert parsed["arch"] == "aarch64_cortex-a53"

    def test_parse_firewall_versions_both_dialects(self, records):
        apk_text = records["apk"]["responses"][
            "apk info -v 2>/dev/null | grep -i '^firewall4'; nft --version 2>/dev/null; iptables -V 2>/dev/null"
        ]
        parsed = ni.parse_firewall_versions(apk_text)
        assert parsed["fw4"].startswith("firewall4-")
        assert parsed["nft"].startswith("nftables v")
        assert "nf_tables" in parsed["iptables"]

        opkg_parsed = ni.parse_firewall_versions(
            records["opkg"]["responses"][
                "opkg list-installed firewall4 2>/dev/null; nft --version 2>/dev/null; iptables -V 2>/dev/null"
            ]
        )
        assert opkg_parsed["fw4"].startswith("firewall4 - ")


# ---------------------------------------------------------------------------
# package-manager detection — never a fallback that can hide failure
# ---------------------------------------------------------------------------


class TestPackageManagerDetection:
    def test_apk_image_detected_explicitly(self, records):
        identity = capture_from(records, "apk")
        assert identity.manager == "apk"
        # the opkg probe was *run* and recorded, not skipped by a `||` chain
        assert identity.evidence["command -v opkg"] == ""

    def test_opkg_image_detected_explicitly(self, records):
        identity = capture_from(records, "opkg")
        assert identity.manager == "opkg"
        assert identity.evidence["command -v apk"] == ""

    def test_no_package_manager_is_an_error_not_a_fallback(self):
        evidence: dict = {}
        with pytest.raises(ni.NdsIdentityError) as excinfo:
            ni.detect_package_manager(ssh_from({"command -v apk": "", "command -v opkg": ""}), evidence)
        assert "no package manager" in str(excinfo.value)

    def test_both_managers_present_is_an_error_not_a_guess(self):
        with pytest.raises(ni.NdsIdentityError) as excinfo:
            ni.detect_package_manager(
                ssh_from({"command -v apk": "/usr/bin/apk\n", "command -v opkg": "/bin/opkg\n"}), {}
            )
        assert "ambiguous" in str(excinfo.value)

    def test_a_failing_probe_is_recorded_and_never_counts_as_present(self):
        def boom(cmd):
            if cmd == "command -v apk":
                raise OSError("connection reset")
            return "/bin/opkg\n"

        evidence: dict = {}
        assert ni.detect_package_manager(boom, evidence) == "opkg"
        assert evidence["command -v apk"].startswith("<error:")


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


class TestCapture:
    def test_apk_capture_is_complete_and_names_the_revision(self, records):
        identity = capture_from(records, "apk")
        assert identity.identity_complete(), identity.problems
        assert identity.package["full"] == "5.0.2-r2"
        assert identity.package["revision"] == "r2"
        assert identity.package["arch"] == "aarch64_cortex-a53"
        assert identity.daemon["version"] == "5.0.2"
        assert all(identity.daemon["binary_sha256"].values())
        assert identity.enforcement["config_sha256"]
        assert identity.enforcement["fragments_sha256"]
        assert identity.platform["kernel"] == "6.12.55"
        assert identity.platform["firewall"]["fw4"].startswith("firewall4-")
        assert identity.problems == []
        assert "5.0.2-r2" in identity.summary_line()

    def test_opkg_capture_is_complete(self, records):
        identity = capture_from(records, "opkg")
        assert identity.identity_complete(), identity.problems
        assert identity.package["full"] == "5.0.2-r1"
        assert identity.package["arch"] == "mipsel_24kc"

    def test_raw_evidence_is_preserved_verbatim(self, records):
        identity = capture_from(records, "apk")
        raw = records["apk"]["responses"]["ndsctl status 2>/dev/null"]
        assert identity.evidence["ndsctl_status"] == raw
        assert identity.evidence["package_version"].strip() == "nodogsplash-5.0.2-r2"

    def test_package_daemon_disagreement_is_reported(self, records):
        identity = capture_from(
            records, "apk",
            overrides={"ndsctl status 2>/dev/null":
                       records["apk"]["responses"]["ndsctl status 2>/dev/null"]
                       .replace("Version: 5.0.2", "Version: 5.0.1")},
        )
        assert any("package/daemon version disagreement" in p for p in identity.problems)

    def test_missing_enforcement_fragments_is_reported(self, records):
        identity = capture_from(
            records, "apk",
            overrides={
                "( iptables -S 2>/dev/null; iptables -t mangle -S 2>/dev/null ) "
                "| grep -E '^(-N|-A|-P|-X) nds' || true": "",
            },
        )
        assert any("no loaded NDS enforcement fragments" in p for p in identity.problems)
        assert not identity.identity_complete()

    def test_an_ssh_failure_mid_capture_is_recorded_not_raised(self, records):
        responses = dict(records["apk"]["responses"])
        del responses["sha256sum /usr/bin/nodogsplash /usr/bin/ndsctl 2>/dev/null"]

        def run_ssh(cmd):
            if cmd not in responses:
                raise OSError("ssh died")
            return responses[cmd]

        identity = ni.capture(run_ssh, phase="deploy", env={}, clock=lambda: "t")
        assert not identity.identity_complete()
        assert any("sha256 of /usr/bin/nodogsplash" in p for p in identity.problems)

    def test_snapshot_roundtrip(self, records, tmp_path):
        identity = capture_from(records, "apk")
        path = ni.write_snapshot(str(tmp_path), identity)
        assert os.path.isfile(path)
        loaded = ni.read_snapshot(str(tmp_path), "deploy")
        assert loaded is not None
        assert loaded.package == identity.package
        assert loaded.daemon["binary_sha256"] == identity.daemon["binary_sha256"]
        assert loaded.enforcement["fragments_sha256"] == identity.enforcement["fragments_sha256"]


# ---------------------------------------------------------------------------
# expectation + drift
# ---------------------------------------------------------------------------


class TestExpectation:
    def test_qualified_identity_has_no_violations(self, records):
        identity = capture_from(records, "apk")
        expected = ni.expected_from(records["qualification"])
        assert ni.identity_violations(identity, expected) == []

    def test_no_expectation_is_not_verified(self, records):
        identity = capture_from(records, "apk")
        problems = ni.identity_violations(identity, ni.expected_from({}))
        assert problems
        assert any("no expected nodogsplash identity" in p for p in problems)

    def test_revision_bump_does_not_inherit_the_pass(self, records):
        """Same upstream version, new packaging revision — a different build."""
        identity = capture_from(records, "apk")
        bumped = json.loads(json.dumps(records["qualification"]))
        bumped["qualified"]["nodogsplash"]["revision"] = "r3"
        problems = ni.identity_violations(identity, ni.expected_from(bumped))
        assert any("packaging-revision mismatch" in p for p in problems)

    def test_version_mismatch_is_named_with_both_sides(self, records):
        identity = capture_from(records, "apk")
        other = json.loads(json.dumps(records["qualification"]))
        other["qualified"]["nodogsplash"]["version"] = "5.0.3"
        other["qualified"]["nodogsplash"]["revision"] = "r1"
        problems = ni.identity_violations(identity, ni.expected_from(other))
        assert any("version mismatch: installed 5.0.2-r2" in p and "5.0.3-r1" in p for p in problems)

    def test_arch_mismatch_is_reported(self, records):
        identity = capture_from(records, "apk")
        other = json.loads(json.dumps(records["qualification"]))
        other["qualified"]["nodogsplash"]["arch"] = "mipsel_24kc"
        assert any("arch mismatch" in p for p in ni.identity_violations(identity, ni.expected_from(other)))

    def test_binary_hash_mismatch_is_reported(self, records):
        identity = capture_from(records, "apk")
        other = json.loads(json.dumps(records["qualification"]))
        other["qualified"]["nodogsplash"]["binary_sha256"]["ndsctl"] = "0" * 64
        problems = ni.identity_violations(identity, ni.expected_from(other))
        assert any("ndsctl sha256 mismatch" in p for p in problems)

    def test_manifest_without_binary_hashes_still_checks_version(self, records):
        identity = capture_from(records, "apk")
        thin = {"qualified": {"nodogsplash": {"version": "5.0.2", "revision": "r2"}}}
        assert ni.identity_violations(identity, ni.expected_from(thin)) == []

    def test_load_qualification_from_path(self, records, tmp_path):
        path = tmp_path / "q.json"
        path.write_text(json.dumps(records["qualification"]))
        loaded = ni.load_qualification(str(path))
        assert loaded["qualified"]["nodogsplash"]["version"] == "5.0.2"

    def test_load_qualification_env_default(self, records, tmp_path, monkeypatch):
        path = tmp_path / "q.json"
        path.write_text(json.dumps(records["qualification"]))
        monkeypatch.setenv(ni.QUALIFICATION_ENV, str(path))
        assert ni.load_qualification()["qualified"]["nodogsplash"]["revision"] == "r2"

    def test_absent_manifest_is_empty_not_an_error(self, monkeypatch, tmp_path):
        monkeypatch.delenv(ni.QUALIFICATION_ENV, raising=False)
        monkeypatch.setattr(ni, "DEFAULT_QUALIFICATION_PATH", str(tmp_path / "absent.json"))
        assert ni.load_qualification() == {}

    def test_committed_default_manifest_qualifies_nothing(self, records):
        """The shipped manifest exists, but qualifies no build — so no pass."""
        path = ni.DEFAULT_QUALIFICATION_PATH
        assert os.path.isfile(path), path
        assert ni.load_qualification(path).get("qualified") == {}

        identity = capture_from(records, "apk")
        expected = ni.expected_from(ni.load_qualification(path))
        assert expected["present"] is False
        assert any("no expected nodogsplash identity" in p
                   for p in ni.identity_violations(identity, expected))

    def test_default_manifest_is_used_when_no_env_is_set(self, records, monkeypatch):
        """A plain checkout resolves the committed manifest, not 'absent'."""
        monkeypatch.delenv(ni.QUALIFICATION_ENV, raising=False)
        loaded = ni.load_qualification()
        assert isinstance(loaded, dict)
        assert "qualified" in loaded

    def test_corrupt_manifest_is_an_error(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json")
        with pytest.raises(ni.NdsIdentityError):
            ni.load_qualification(str(path))


class TestDrift:
    def test_identical_snapshots_do_not_drift(self, records):
        before = capture_from(records, "apk", phase="deploy")
        after = capture_from(records, "apk", phase="teardown")
        assert ni.drift_violations(before, after) == []

    def test_volatile_status_fields_are_excluded_from_drift(self, records):
        before = capture_from(records, "apk", phase="deploy")
        after = capture_from(
            records, "apk", phase="teardown",
            overrides={"ndsctl status 2>/dev/null":
                       records["apk"]["responses"]["ndsctl status 2>/dev/null"]
                       .replace("Uptime: 0d 0h 12m 31s", "Uptime: 0d 1h 2m 0s")
                       .replace("Total download: 482 kByte; avg: 5.12 kbit/s",
                                "Total download: 78120 kByte; avg: 90.0 kbit/s")},
        )
        assert ni.drift_violations(before, after) == []

    def test_config_change_between_snapshots_is_drift(self, records):
        before = capture_from(records, "apk", phase="deploy")
        after = capture_from(
            records, "apk", phase="teardown",
            overrides={"sha256sum /etc/config/nodogsplash 2>/dev/null": "f" * 64 + "  /etc/config/nodogsplash\n"},
        )
        problems = ni.drift_violations(before, after)
        assert any("enforcement.config_sha256" in p for p in problems)

    def test_enforcement_rule_change_is_drift(self, records):
        before = capture_from(records, "apk", phase="deploy")
        after = capture_from(
            records, "apk", phase="teardown",
            overrides={"( iptables -S 2>/dev/null; iptables -t mangle -S 2>/dev/null ) "
                       "| grep -E '^(-N|-A|-P|-X) nds' || true":
                       "-N ndsNET\n-A ndsNET -m mark --mark 0x20000/0x20000 -j ACCEPT\n"},
        )
        assert any("enforcement.fragments_sha256" in p for p in ni.drift_violations(before, after))

    def test_binary_replaced_mid_run_is_drift(self, records):
        before = capture_from(records, "apk", phase="deploy")
        after = capture_from(
            records, "apk", phase="teardown",
            overrides={"sha256sum /usr/bin/nodogsplash /usr/bin/ndsctl 2>/dev/null":
                       "9" * 64 + "  /usr/bin/nodogsplash\n"
                       + "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809  /usr/bin/ndsctl\n"},
        )
        problems = ni.drift_violations(before, after)
        assert any("identity drift: nodogsplash sha256 changed" in p for p in problems)


# ---------------------------------------------------------------------------
# the report block + mock rejection
# ---------------------------------------------------------------------------


class TestDependencyBlock:
    def test_verified_block_names_version_revision_and_hashes(self, records):
        block = qualified_block(records)
        assert block["status"] == "VERIFIED", block["problems"]
        assert block["version"] == "5.0.2-r2"
        assert block["revision"] == "r2"
        assert block["manager"] == "apk"
        assert block["arch"] == "aarch64_cortex-a53"
        assert block["daemon_version"] == "5.0.2"
        assert all(block["binary_sha256"].values())
        assert block["config_sha256"]
        assert block["enforcement_fragments_sha256"]
        assert block["evidence"]["deploy"].endswith("deploy.json")
        assert block["evidence"]["teardown"].endswith("teardown.json")
        assert "5.0.2-r2" in block["summary"]

    def test_no_snapshots_at_all_is_not_verified(self):
        block = ni.build_dependency_block(None, None, qualification={"qualified": {}}, env={})
        assert block["status"] == "NOT_VERIFIED"
        assert block["recorded"] is False
        assert any("no nodogsplash identity was recorded" in p for p in block["problems"])

    def test_only_one_snapshot_cannot_rule_out_drift(self, records):
        before = capture_from(records, "apk", phase="deploy")
        block = ni.build_dependency_block(
            before, None, qualification=records["qualification"], env={}
        )
        assert block["status"] == "NOT_VERIFIED"
        assert any("only one identity snapshot" in p for p in block["problems"])

    def test_a_verified_identity_expectation_beats_no_expectation(self, records):
        """Without a manifest the identity is captured but still NOT VERIFIED."""
        before = capture_from(records, "apk", phase="deploy")
        after = capture_from(records, "apk", phase="teardown")
        block = ni.build_dependency_block(before, after, qualification={}, env={})
        assert block["status"] == "NOT_VERIFIED"
        assert block["expected"]["present"] is False
        assert any("no expected nodogsplash identity" in p for p in block["problems"])
        # ... and the observed identity is still spelled out for the operator
        assert block["version"] == "5.0.2-r2"

    def test_opkg_flavour_against_the_apk_qualification_fails(self, records):
        block = qualified_block(records, flavor="opkg")
        assert block["status"] == "NOT_VERIFIED"
        assert any("version mismatch" in p or "revision mismatch" in p for p in block["problems"])


class TestMockRejection:
    def test_require_release_evidence_raises_in_mock_mode(self):
        with pytest.raises(ni.NdsIdentityError) as excinfo:
            ni.require_release_evidence({ni.MOCK_ENV: "1"}, context="collect-results")
        assert "refusing to emit release evidence" in str(excinfo.value)

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " 1 "])
    def test_mock_truthiness_matches_the_fixture_flag(self, value):
        with pytest.raises(ni.NdsIdentityError):
            ni.require_release_evidence({ni.MOCK_ENV: value})

    def test_require_release_evidence_passes_on_a_real_run(self):
        assert ni.require_release_evidence({ni.MOCK_ENV: ""}) is None
        assert ni.require_release_evidence({}) is None

    def test_capture_in_mock_mode_records_the_reason(self, records):
        identity = capture_from(records, "apk", env={ni.MOCK_ENV: "1"}, strict=False)
        assert identity.daemon.get("mock_mode") is True
        assert not identity.identity_complete()
        assert any("mock router has no installed nodogsplash" in p for p in identity.problems)


# ---------------------------------------------------------------------------
# NEGATIVE CONTROLS — the three planted failures the card asks for
# ---------------------------------------------------------------------------


class TestNegativeControls:
    """Each control plants ONE defect and asserts the run fails for THAT reason.

    These are the acceptance criterion for the release gate.  They are proven
    here against recorded fixtures, so they need no router: the same code path
    that judges a bench run judges these.
    """

    def test_control_1_missing_version_fails(self, records):
        """The package query returns nothing → NOT VERIFIED, naming the gap."""
        block = qualified_block(
            records,
            overrides={"apk info -v 2>/dev/null | grep -i '^nodogsplash'": ""},
        )
        assert block["status"] == "NOT_VERIFIED"
        assert block["version"] == ""
        reasons = " | ".join(block["problems"])
        assert "package version missing" in reasons
        assert "identity incomplete" in reasons
        # the failure names the command an operator must run to see it
        assert "apk info -v" in reasons

    def test_control_2_version_mismatch_fails(self, records):
        """A newer feed version is installed than the qualified one."""
        block = qualified_block(
            records,
            overrides={"apk info -v 2>/dev/null | grep -i '^nodogsplash'": "nodogsplash-5.0.3-r1\n"},
        )
        assert block["status"] == "NOT_VERIFIED"
        assert block["version"] == "5.0.3-r1"
        reasons = " | ".join(block["problems"])
        assert "version mismatch: installed 5.0.3-r1" in reasons
        assert "5.0.2-r2" in reasons
        assert "does not inherit the old pass" in reasons

    def test_control_3_mock_mode_fails(self, records):
        """A mock run is refused mechanically, and no identity is invented."""
        identity = capture_from(records, "apk", env={ni.MOCK_ENV: "1"}, strict=False)
        block = ni.build_dependency_block(
            identity, identity,
            qualification=records["qualification"],
            release_evidence=True,
            env={ni.MOCK_ENV: "1"},
        )
        assert block["status"] == "NOT_VERIFIED"
        assert block["mock_mode"] is True
        assert any("mock runs cannot produce release evidence" in p for p in block["problems"])
        with pytest.raises(ni.NdsIdentityError):
            ni.require_release_evidence({ni.MOCK_ENV: "1"})
