"""Dependency identity for the nodogsplash (NDS) under test.

A green NDS run used to certify nothing in particular: ``RouterProbe``
(lib/preflight.py) recorded device/firmware/TollGate version but for
nodogsplash only ``nodogsplash_running``. Two runs a package revision apart
produced identical-looking evidence, so a pass could not be attributed to a
shipped artifact.

This module makes every NDS run name what it tested:

  package        full version *including the packaging revision* (``5.0.2-r2``),
                 the package manager (detected with ``command -v``, never a
                 fallback chain that can hide a failure) and the architecture
  daemon         the version the *running daemon* reports (raw ``ndsctl
                 status``), plus sha256 of the installed ``nodogsplash`` and
                 ``ndsctl`` binaries
  platform       firmware/kernel and the firewall tooling versions (fw4,
                 nft, iptables-nft)
  enforcement    sha256 of the NDS config and of the loaded NDS enforcement
                 fragments (the nds* chains as the kernel actually holds them)

Capture is run twice — at deploy time and again before teardown — and the two
snapshots are compared: an unexplained drift is a violation, because either
the artifact changed under the run or something rewrote the enforcement.

An expected identity must come from the campaign's selected dependency or an
explicit qualification manifest (:func:`load_qualification`).  A missing
identity is ``NOT_VERIFIED`` and can never pass.  A new feed version does not
inherit an old pass: the expectation names version AND revision AND (when
given) the binary hashes, so a revision bump misses it by construction.

``TOLLGATE_MOCK=1`` is rejected mechanically (:func:`require_release_evidence`):
a mock router has no binaries to hash, so its "identity" would be fiction.

Everything here is injectable through ``run_ssh`` (see lib/preflight.py's
``RunSsh``), so the whole contract is unit-testable against recorded fixtures
with no router.

TODO(unverified) — the probe commands in :data:`PROBES` are exercised against
recorded fixtures only.  On hardware, confirm each one on the pinned bench
image and paste the raw outputs into the qualification manifest::

    ssh root@<bench> 'command -v apk; command -v opkg'
    ssh root@<bench> "apk info -v 2>/dev/null | grep -i '^nodogsplash'"     # apk image
    ssh root@<bench> "opkg list-installed nodogsplash 2>/dev/null"          # opkg image
    ssh root@<bench> 'ndsctl status | head -5'
    ssh root@<bench> 'sha256sum /usr/bin/nodogsplash /usr/bin/ndsctl'
    ssh root@<bench> 'sha256sum /etc/config/nodogsplash'
    ssh root@<bench> 'nft --version; iptables -V; apk info -v 2>/dev/null | grep firewall4'
    ssh root@<bench> 'iptables -S 2>/dev/null | grep -E "^(-N|-A) nds"'
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Callable

RunSsh = Callable[[str], str]

#: the dependency this module is about
NDS_PACKAGE = "nodogsplash"

#: installed paths the OpenWrt package installs (packages/net/nodogsplash/Makefile:
#: ``$(INSTALL_BIN) $(PKG_BUILD_DIR)/nodogsplash $(1)/usr/bin/`` and the same for ndsctl)
NDS_BINARIES = {
    "nodogsplash": "/usr/bin/nodogsplash",
    "ndsctl": "/usr/bin/ndsctl",
}

NDS_CONFIG = "/etc/config/nodogsplash"

#: package managers we can be installed by; each is probed with ``command -v``
PACKAGE_MANAGERS = ("apk", "opkg")

MOCK_ENV = "TOLLGATE_MOCK"
QUALIFICATION_ENV = "TOLLGATE_NDS_QUALIFICATION"

#: repo root, for the default qualification-manifest location
REPO_ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))

#: the default qualification manifest, committed with the repo.  It ships with an
#: empty ``qualified`` map, so a fresh checkout reports NOT VERIFIED rather than
#: silently qualifying whatever happens to be installed.
DEFAULT_QUALIFICATION_PATH = os.path.join(REPO_ROOT, "config", "nds-qualification.json")

#: where a run writes its two identity snapshots (relative to the results dir)
IDENTITY_DIRNAME = os.path.join("raw", "nds-identity")
DEPLOY_SNAPSHOT = "deploy.json"
TEARDOWN_SNAPSHOT = "teardown.json"

#: fields whose change between deploy and teardown is *explained* (uptime, the
#: client list, traffic counters).  Everything else must be identical.
VOLATILE_STATUS_FIELDS = (
    "Uptime",
    "Total download",
    "Total upload",
    "Client authentications since start",
)

STATUS_NOISE_RE = re.compile(r"^(?:-+\w+ *\w+ *)?$")


class NdsIdentityError(RuntimeError):
    """Identity could not be established at all (as opposed to "is wrong")."""


# ---------------------------------------------------------------------------
# probe commands
# ---------------------------------------------------------------------------


def package_manager_probe_command(manager: str) -> str:
    """``command -v <manager>`` — explicit, one manager at a time.

    Deliberately not a fallback chain: a chain (``command -v apk || command -v
    opkg``) cannot tell "apk is absent" from "apk is present but the image is
    broken", and silently reports the second as opkg.
    """
    if manager not in PACKAGE_MANAGERS:
        raise NdsIdentityError(f"unknown package manager {manager!r} (expected {PACKAGE_MANAGERS})")
    return f"command -v {manager}"


def package_version_command(manager: str, package: str = NDS_PACKAGE) -> str:
    if manager == "apk":
        return f"apk info -v 2>/dev/null | grep -i '^{package}'"
    if manager == "opkg":
        return f"opkg list-installed {package} 2>/dev/null"
    raise NdsIdentityError(f"unknown package manager {manager!r}")


def arch_command(manager: str) -> str:
    if manager == "apk":
        return "apk --print-arch 2>/dev/null"
    return "opkg print-architecture 2>/dev/null | tail -1 | awk '{print $2}'"


def openwrt_release_command() -> str:
    return (
        '. /etc/openwrt_release 2>/dev/null; '
        'echo "$DISTRIB_ID|$DISTRIB_RELEASE|$DISTRIB_TARGET|$DISTRIB_ARCH|$DISTRIB_DESCRIPTION"'
    )


def kernel_command() -> str:
    return "uname -r"


def firewall_version_command(manager: str) -> str:
    """fw4 (the runner), nft, and iptables — the three that gate NDS traffic."""
    fw4 = (
        f"apk info -v 2>/dev/null | grep -i '^firewall4'"
        if manager == "apk"
        else "opkg list-installed firewall4 2>/dev/null"
    )
    return f"{fw4}; nft --version 2>/dev/null; iptables -V 2>/dev/null"


def daemon_status_command() -> str:
    return "ndsctl status 2>/dev/null"


def binary_hash_command() -> str:
    paths = " ".join(NDS_BINARIES.values())
    return f"sha256sum {paths} 2>/dev/null"


def config_hash_command() -> str:
    return f"sha256sum {NDS_CONFIG} 2>/dev/null"


def enforcement_fragment_command() -> str:
    """The loaded NDS enforcement chains, as the kernel holds them.

    ``iptables -S`` on an iptables-nft image reads the nft ruleset, so this is
    the live enforcement, not a config file.  Counters are excluded (no
    ``-v``) so the digest is comparable across the run — only rule *text* is
    hashed, which is what the gating assertions reason about.
    """
    return (
        "( iptables -S 2>/dev/null; iptables -t mangle -S 2>/dev/null ) "
        "| grep -E '^(-N|-A|-P|-X) nds' || true"
    )


def nft_fragment_list_command() -> str:
    """NDS-dropped nft includes under /etc/nftables.d (fw4 include files)."""
    return (
        "for f in /etc/nftables.d/*; do case \"$f\" in *nds*) "
        "echo \"--- $f\"; cat \"$f\"; esac; done 2>/dev/null || true"
    )


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def parse_package_version(text: str, manager: str, package: str = NDS_PACKAGE) -> dict:
    """``nodogsplash-5.0.2-r2`` (apk) / ``nodogsplash - 5.0.2-r2`` (opkg) → fields.

    The packaging revision (``-r2``) is always separated out: two runs that
    differ only in the revision are different builds and must not share a
    qualification.  ``full`` is the version token exactly as the manager printed
    it, so a suffixed revision (``-r2.1``) is preserved instead of being
    truncated to something that looks like a clean release.
    """
    raw = (text or "").strip()
    first = raw.splitlines()[0].strip() if raw else ""
    version = revision = ""
    token = ""
    if manager == "apk":
        match = re.match(rf"^{re.escape(package)}-(\d\S*)$", first)
    elif manager == "opkg":
        match = re.match(rf"^{re.escape(package)}\s+-\s+(\d\S*)$", first)
    else:
        raise NdsIdentityError(f"unknown package manager {manager!r}")
    if match:
        token = match.group(1)
        split = re.match(r"^(.+?)-r(\d+(?:\.\d+)*)$", token)
        if split:
            version, revision = split.group(1), split.group(2)
        else:
            version = token
    full = f"{version}-r{revision}" if (version and revision) else version
    return {
        "manager": manager,
        "package": package,
        "raw": raw,
        "version": version,
        "revision": f"r{revision}" if revision else "",
        "full": full,
    }


def parse_daemon_version(status_text: str) -> str:
    """Version the running daemon reports, from raw ``ndsctl status``.

    nodogsplash v5.0.2 ``ndsctl_status()`` (src/util.c) prints, verbatim::

        ==================
        NoDogSplash Status
        ====
        Version: 5.0.2
        Uptime: ...

    The daemon version is what it was *compiled* from; it is recorded next to
    the package version precisely so a repackaged binary that claims a version
    it is not built from shows up as a disagreement.
    """
    for line in (status_text or "").splitlines():
        match = re.match(r"^\s*Version:\s*(\S+)\s*$", line)
        if match:
            return match.group(1)
    return ""


def stable_status_text(status_text: str) -> str:
    """``ndsctl status`` with the by-design volatile lines removed."""
    kept = []
    for line in (status_text or "").splitlines():
        if any(line.strip().startswith(field) for field in VOLATILE_STATUS_FIELDS):
            continue
        kept.append(line.rstrip())
    return "\n".join(kept).strip()


def parse_openwrt_release(text: str) -> dict:
    parts = (text or "").strip().split("|")
    parts += [""] * (5 - len(parts))
    return {
        "id": parts[0].strip(),
        "release": parts[1].strip(),
        "target": parts[2].strip(),
        "arch": parts[3].strip(),
        "description": parts[4].strip(),
    }


def parse_sha256sums(text: str) -> dict[str, str]:
    """``sha256sum`` output → ``{path: digest}`` (first field wins per path)."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        match = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+)$", line.strip())
        if not match:
            continue
        out[match.group(2).strip()] = match.group(1).lower()
    return out


def parse_firewall_versions(text: str) -> dict:
    """Parse the ``firewall_version_command`` output (package line + two tools)."""
    fw4 = nft = iptables = ""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("firewall4-"):
            fw4 = stripped
        elif stripped.lower().startswith("nftables") or stripped.startswith("nft v"):
            # `nft --version` → "nftables v1.0.9 (Old Doc Yak #3)" on 25.x;
            # older builds print "nft v1.0.6 (Lester Gooch #5)"
            nft = stripped
        elif stripped.lower().startswith("iptables v"):
            iptables = stripped
        elif re.match(r"^firewall4\s+-\s+", stripped):
            fw4 = stripped
    return {"fw4": fw4, "nft": nft, "iptables": iptables}


def digest_text(text: str) -> str:
    """sha256 of a normalized text block (trailing whitespace stripped per line)."""
    normalized = "\n".join(line.rstrip() for line in (text or "").splitlines()).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


@dataclass
class NdsIdentity:
    """One identity snapshot: raw evidence + the parsed fields derived from it."""

    phase: str = ""
    captured_at: str = ""
    package: dict = field(default_factory=dict)
    daemon: dict = field(default_factory=dict)
    platform: dict = field(default_factory=dict)
    enforcement: dict = field(default_factory=dict)
    manager: str = ""
    evidence: dict = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "schema_version": 1,
            "phase": self.phase,
            "captured_at": self.captured_at,
            "manager": self.manager,
            "package": self.package,
            "daemon": self.daemon,
            "platform": self.platform,
            "enforcement": self.enforcement,
            "problems": self.problems,
            "evidence": self.evidence,
        }

    @classmethod
    def from_json(cls, payload: dict) -> "NdsIdentity":
        return cls(
            phase=payload.get("phase", ""),
            captured_at=payload.get("captured_at", ""),
            package=payload.get("package") or {},
            daemon=payload.get("daemon") or {},
            platform=payload.get("platform") or {},
            enforcement=payload.get("enforcement") or {},
            manager=payload.get("manager", ""),
            evidence=payload.get("evidence") or {},
            problems=list(payload.get("problems") or []),
        )

    def identity_complete(self) -> bool:
        """Everything a run must know before it can be attributed."""
        return (
            bool(self.package.get("full"))
            and bool(self.package.get("revision"))
            and bool(self.package.get("arch"))
            and bool(self.daemon.get("version"))
            and all(self.daemon.get("binary_sha256", {}).get(name) for name in NDS_BINARIES)
            and bool(self.enforcement.get("config_sha256"))
            and bool(self.enforcement.get("fragments_sha256"))
        )

    def summary_line(self) -> str:
        pkg = self.package.get("full") or "?"
        arch = self.package.get("arch") or "?"
        daemon = self.daemon.get("version") or "?"
        return f"nodogsplash {pkg} ({self.manager or '?'}, {arch}), daemon {daemon}"


def detect_package_manager(run_ssh: RunSsh, evidence: dict) -> str:
    """Which manager owns this image — explicit probes, ambiguity is an error.

    Both present → the image is ambiguous and the identity cannot be trusted;
    neither present → the image has no package manager at all.  Neither case
    falls back to the other manager.
    """
    found = []
    for manager in PACKAGE_MANAGERS:
        cmd = package_manager_probe_command(manager)
        try:
            out = run_ssh(cmd)
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            evidence[f"command -v {manager}"] = f"<error: {exc}>"
            continue
        evidence[f"command -v {manager}"] = out.strip()
        if out.strip():
            found.append(manager)
    if len(found) == 1:
        return found[0]
    if not found:
        raise NdsIdentityError(
            "neither `command -v apk` nor `command -v opkg` answered on the router: "
            "the image has no package manager, so the installed nodogsplash cannot be "
            f"attributed to a package. Probes: {sorted(evidence)}"
        )
    raise NdsIdentityError(
        "both apk and opkg are present on the router — the package owner is ambiguous, and "
        "guessing one would hide which manager installed nodogsplash. Refusing to record an "
        f"identity. Probes: {evidence}"
    )


def capture(
    run_ssh: RunSsh,
    *,
    phase: str = "",
    env: dict[str, str] | None = None,
    clock: Callable[[], str] | None = None,
) -> NdsIdentity:
    """Probe one identity snapshot. Never raises for a *partial* result.

    A failing probe becomes a recorded ``problem`` (and the corresponding field
    stays empty), so an incomplete identity is reported as NOT VERIFIED rather
    than crashing the run mid-teardown — except for the package-manager
    ambiguity case, which is a hard :class:`NdsIdentityError`, because there is
    no defensible identity to record at all.
    """
    import time as _time

    env = env if env is not None else dict(os.environ)
    ident = NdsIdentity(
        phase=phase,
        captured_at=(clock or (lambda: _time.strftime("%Y-%m-%d %H:%M:%S")))(),
    )

    def probe(label: str, cmd: str) -> str:
        try:
            out = run_ssh(cmd)
        except Exception as exc:  # noqa: BLE001
            ident.evidence[label] = f"<error: {exc}>"
            ident.problems.append(f"{label}: probe failed ({exc})")
            return ""
        ident.evidence[label] = out
        return out

    if env.get(MOCK_ENV, "").strip().lower() in ("1", "true", "yes"):
        ident.problems.append(
            f"{MOCK_ENV}=1 — a mock router has no installed nodogsplash to identify. "
            "Mock runs cannot produce release evidence; rerun against a real router."
        )
        ident.daemon["mock_mode"] = True
        return ident

    try:
        ident.manager = detect_package_manager(run_ssh, ident.evidence)
    except NdsIdentityError as exc:
        ident.problems.append(str(exc))
        return ident

    version_text = probe("package_version", package_version_command(ident.manager))
    ident.package = parse_package_version(version_text, ident.manager)
    if not ident.package.get("full"):
        ident.problems.append(
            f"package version missing: `{package_version_command(ident.manager)}` returned "
            f"{version_text.strip()!r} — nodogsplash is not installed by {ident.manager} "
            "(or the query failed), so this run cannot name what it tested"
        )

    arch = probe("arch", arch_command(ident.manager)).strip().splitlines()
    ident.package["arch"] = arch[0].strip() if arch else ""

    status_text = probe("ndsctl_status", daemon_status_command())
    ident.daemon["version"] = parse_daemon_version(status_text)
    ident.daemon["status_stable_sha256"] = digest_text(stable_status_text(status_text))
    if not ident.daemon["version"]:
        ident.problems.append(
            "daemon-reported version missing: raw `ndsctl status` did not contain a "
            "'Version:' line — the daemon is not answering or is not nodogsplash"
        )

    hashes = parse_sha256sums(probe("binary_sha256", binary_hash_command()))
    ident.daemon["binary_sha256"] = {
        name: hashes.get(path, "") for name, path in NDS_BINARIES.items()
    }
    for name, path in NDS_BINARIES.items():
        if not ident.daemon["binary_sha256"][name]:
            ident.problems.append(f"sha256 of {path} could not be read — binary identity unknown")

    release = parse_openwrt_release(probe("openwrt_release", openwrt_release_command()))
    ident.platform = {
        "openwrt": release,
        "kernel": probe("kernel", kernel_command()).strip(),
        "firewall": parse_firewall_versions(probe("firewall_versions", firewall_version_command(ident.manager))),
    }
    if not release["release"]:
        ident.problems.append("/etc/openwrt_release could not be read — firmware identity unknown")

    config_hashes = parse_sha256sums(probe("nds_config", config_hash_command()))
    fragments_text = probe("enforcement_fragments", enforcement_fragment_command())
    nft_text = probe("nft_fragments", nft_fragment_list_command())
    ident.enforcement = {
        "config_path": NDS_CONFIG,
        "config_sha256": config_hashes.get(NDS_CONFIG, ""),
        "fragments_sha256": digest_text(fragments_text) if fragments_text.strip() else "",
        "fragments_lines": len([line for line in fragments_text.splitlines() if line.strip()]),
        "nft_include_sha256": digest_text(nft_text) if nft_text.strip() else "",
    }
    if not ident.enforcement["config_sha256"]:
        ident.problems.append(f"sha256 of {NDS_CONFIG} could not be read — NDS config identity unknown")
    if not ident.enforcement["fragments_sha256"]:
        ident.problems.append(
            "no loaded NDS enforcement fragments found (empty `iptables -S | grep nds`) — "
            "the chain the gating assertions reason about was not loaded at capture time"
        )

    if ident.package.get("version") and ident.daemon.get("version"):
        if ident.package["version"] != ident.daemon["version"]:
            ident.problems.append(
                f"package/daemon version disagreement: the {ident.manager} package is "
                f"{ident.package['full']} but the running daemon reports "
                f"{ident.daemon['version']} — the binary is not the packaged one"
            )

    return ident


# ---------------------------------------------------------------------------
# expectation (campaign selection or qualification manifest)
# ---------------------------------------------------------------------------


def load_qualification(path: str | None = None, env: dict[str, str] | None = None) -> dict:
    """Load the explicit qualification manifest (``{package: {version, ...}}``).

    Shape::

        {
          "schema_version": 1,
          "qualified": {
            "nodogsplash": {
              "version": "5.0.2",
              "revision": "r2",
              "arch": "aarch64_cortex-a53",
              "binary_sha256": {"nodogsplash": "...", "ndsctl": "..."},
              "campaign": "v0.6.0-alpha4-pre17",
              "qualified_on": "2026-10-20",
              "evidence": "docs/..."
            }
          }
        }

    Returns ``{}`` when there is no manifest — the caller reports the source as
    ``absent`` and the identity as NOT VERIFIED, never as a pass.

    Resolution order: explicit ``path`` → ``$TOLLGATE_NDS_QUALIFICATION`` → the
    committed :data:`DEFAULT_QUALIFICATION_PATH`.  The default file ships with an
    empty ``qualified`` map, so a fresh checkout yields ``{"qualified": {}}`` and
    therefore NOT VERIFIED — presence of the file is never itself a pass.
    """
    env = env if env is not None else dict(os.environ)
    path = path or env.get(QUALIFICATION_ENV, "")
    if not path:
        path = DEFAULT_QUALIFICATION_PATH if os.path.isfile(DEFAULT_QUALIFICATION_PATH) else ""
    if not path:
        return {}
    try:
        with open(path) as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise NdsIdentityError(f"cannot read NDS qualification manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise NdsIdentityError(f"NDS qualification manifest {path} is not a JSON object")
    return payload


def expected_from(
    qualification: dict,
    *,
    package: str = NDS_PACKAGE,
    source: str = "",
) -> dict:
    """The expected identity for one package, with its provenance."""
    qualified = (qualification or {}).get("qualified") or {}
    entry = qualified.get(package)
    if not entry:
        return {"source": source or "absent", "present": False}
    expected = dict(entry)
    expected["source"] = source or expected.get("evidence") or "qualification manifest"
    expected["present"] = True
    return expected


def identity_violations(observed: NdsIdentity, expected: dict | None = None) -> list[str]:
    """Why this snapshot is not attributable — empty list means verified.

    A missing expectation is itself a violation: an unqualified install is not
    a pass, it is an untested dependency that happens to be running.
    """
    problems = list(observed.problems)
    if not observed.identity_complete():
        stated = "; ".join(problems) if problems else "one or more identity fields are empty"
        problems.append(f"identity incomplete — NOT VERIFIED ({stated})")
        return problems

    expected = expected or {}
    if not expected.get("present"):
        problems.append(
            "no expected nodogsplash identity for this run: qualify the package "
            "(config/nds-qualification.json, TOLLGATE_NDS_QUALIFICATION, or the "
            "campaign's selected dependency) — an unqualified version is NOT VERIFIED"
        )
        return problems

    want_version = str(expected.get("version", "") or "")
    want_revision = str(expected.get("revision", "") or "")
    if want_version and observed.package.get("version") != want_version:
        problems.append(
            f"version mismatch: installed {observed.package.get('full') or '?'} but the "
            f"qualified dependency is {want_version}{'-' + want_revision if want_revision else ''} "
            f"(source: {expected.get('source', '?')}) — a new feed version enters qualification, "
            "it does not inherit the old pass"
        )
    elif want_revision and observed.package.get("revision") != want_revision:
        problems.append(
            f"packaging-revision mismatch: installed {observed.package.get('full') or '?'} but the "
            f"qualified dependency is {want_version}-{want_revision} "
            f"(source: {expected.get('source', '?')}) — same upstream version, different build"
        )

    want_arch = str(expected.get("arch", "") or "")
    if want_arch and observed.package.get("arch") != want_arch:
        problems.append(
            f"arch mismatch: installed {observed.package.get('arch') or '?'} but the qualified "
            f"dependency is {want_arch} (source: {expected.get('source', '?')})"
        )

    want_hashes = expected.get("binary_sha256") or {}
    observed_hashes = observed.daemon.get("binary_sha256") or {}
    for name, want in want_hashes.items():
        want = (want or "").strip().lower()
        if not want:
            continue
        got = (observed_hashes.get(name) or "").strip().lower()
        if got != want:
            problems.append(
                f"{name} sha256 mismatch: installed {got or '<unreadable>'} != qualified {want} "
                f"(source: {expected.get('source', '?')}) — the running binary is not the "
                "qualified build"
            )

    want_daemon = str(expected.get("daemon_version", "") or "")
    if want_daemon and observed.daemon.get("version") != want_daemon:
        problems.append(
            f"daemon version mismatch: ndsctl reports {observed.daemon.get('version') or '?'} but "
            f"the qualified dependency reports {want_daemon}"
        )
    return problems


def drift_violations(before: NdsIdentity, after: NdsIdentity) -> list[str]:
    """Unexplained differences between the deploy-time and pre-teardown snapshots.

    The run is only attributable if the thing it tested at the end is the thing
    it installed at the start.  ``ndsctl status`` fields that move on their own
    (uptime, traffic counters) are excluded in :func:`stable_status_text`; their
    exclusion is the *only* allowance.
    """
    problems: list[str] = []
    checks = (
        ("package.full", before.package.get("full"), after.package.get("full")),
        ("package.revision", before.package.get("revision"), after.package.get("revision")),
        ("package.arch", before.package.get("arch"), after.package.get("arch")),
        ("daemon.version", before.daemon.get("version"), after.daemon.get("version")),
        ("platform.kernel", before.platform.get("kernel"), after.platform.get("kernel")),
        (
            "enforcement.config_sha256",
            before.enforcement.get("config_sha256"),
            after.enforcement.get("config_sha256"),
        ),
        (
            "enforcement.fragments_sha256",
            before.enforcement.get("fragments_sha256"),
            after.enforcement.get("fragments_sha256"),
        ),
        (
            "enforcement.nft_include_sha256",
            before.enforcement.get("nft_include_sha256"),
            after.enforcement.get("nft_include_sha256"),
        ),
        (
            "daemon.status_stable_sha256",
            before.daemon.get("status_stable_sha256"),
            after.daemon.get("status_stable_sha256"),
        ),
    )
    for label, was, now in checks:
        if was != now:
            problems.append(
                f"identity drift: {label} changed between deploy ({was or '<empty>'}) and "
                f"teardown ({now or '<empty>'}) — the artifact or its enforcement moved under "
                "the run, so neither snapshot describes what the tests exercised"
            )
    for name in NDS_BINARIES:
        was = (before.daemon.get("binary_sha256") or {}).get(name)
        now = (after.daemon.get("binary_sha256") or {}).get(name)
        if was != now:
            problems.append(
                f"identity drift: {name} sha256 changed between deploy ({was or '<unreadable>'}) "
                f"and teardown ({now or '<unreadable>'})"
            )
    return problems


# ---------------------------------------------------------------------------
# snapshots on disk + the report block
# ---------------------------------------------------------------------------


def snapshot_path(results_dir: str, phase: str) -> str:
    name = DEPLOY_SNAPSHOT if phase == "deploy" else TEARDOWN_SNAPSHOT
    return os.path.join(results_dir, IDENTITY_DIRNAME, name)


def write_snapshot(results_dir: str, identity: NdsIdentity) -> str:
    path = snapshot_path(results_dir, identity.phase)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        json.dump(identity.to_json(), handle, indent=2)
        handle.write("\n")
    return path


def read_snapshot(results_dir: str, phase: str) -> NdsIdentity | None:
    path = snapshot_path(results_dir, phase)
    try:
        with open(path) as handle:
            return NdsIdentity.from_json(json.load(handle))
    except (OSError, json.JSONDecodeError):
        return None


def load_run_identity(results_dir: str) -> tuple[NdsIdentity | None, NdsIdentity | None]:
    """Read the two snapshots this run wrote (either may be absent)."""
    return read_snapshot(results_dir, "deploy"), read_snapshot(results_dir, "teardown")


def build_dependency_block(
    before: NdsIdentity | None,
    after: NdsIdentity | None,
    *,
    qualification: dict | None = None,
    release_evidence: bool = False,
    env: dict[str, str] | None = None,
) -> dict:
    """The ``sut.dependencies.nodogsplash`` block for run.json.

    Status is one of:

      ``VERIFIED``      captured twice, identical where it must be, and equal to
                        the expected identity from the campaign/manifest
      ``NOT_VERIFIED``  anything else — including "no identity was recorded at
                        all", which is the commonest way an NDS result used to
                        look green while certifying nothing
    """
    env = env if env is not None else dict(os.environ)
    qualification = qualification if qualification is not None else {}
    source = (
        env.get(QUALIFICATION_ENV)
        or (qualification.get("source") if isinstance(qualification, dict) else "")
        or "absent"
    )
    expected = expected_from(qualification, source=source)

    block: dict = {
        "package": NDS_PACKAGE,
        "status": "NOT_VERIFIED",
        "problems": [],
        "recorded": bool(before or after),
        "release_evidence": bool(release_evidence),
        "expected": expected if expected.get("present") else {"present": False, "source": source},
        "mock_mode": bool(env.get(MOCK_ENV, "").strip().lower() in ("1", "true", "yes")),
    }

    if block["mock_mode"]:
        block["problems"].append(
            f"{MOCK_ENV}=1 — mock runs cannot produce release evidence for an installed dependency"
        )

    if before is None and after is None:
        block["problems"].append(
            "no nodogsplash identity was recorded for this run (expected "
            f"{IDENTITY_DIRNAME}/{DEPLOY_SNAPSHOT} and {TEARDOWN_SNAPSHOT}) — the run cannot "
            "name the package version or binary hashes it tested"
        )
        return block
    if before is None or after is None:
        missing = "deploy" if before is None else "teardown"
        block["problems"].append(
            f"only one identity snapshot was recorded (the {missing} one is missing) — "
            "drift cannot be ruled out"
        )

    primary = before or after
    assert primary is not None  # guarded by the branch above
    block["manager"] = primary.manager
    block["version"] = primary.package.get("full", "")
    block["version_number"] = primary.package.get("version", "")
    block["revision"] = primary.package.get("revision", "")
    block["arch"] = primary.package.get("arch", "")
    block["daemon_version"] = primary.daemon.get("version", "")
    block["binary_sha256"] = dict(primary.daemon.get("binary_sha256") or {})
    block["firmware"] = (primary.platform.get("openwrt") or {}).get("description", "")
    block["kernel"] = primary.platform.get("kernel", "")
    block["firewall"] = dict(primary.platform.get("firewall") or {})
    block["config_sha256"] = primary.enforcement.get("config_sha256", "")
    block["enforcement_fragments_sha256"] = primary.enforcement.get("fragments_sha256", "")
    block["evidence"] = {
        "deploy": f"{IDENTITY_DIRNAME}/{DEPLOY_SNAPSHOT}",
        "teardown": f"{IDENTITY_DIRNAME}/{TEARDOWN_SNAPSHOT}",
    }

    block["problems"].extend(identity_violations(primary, expected))
    if before is not None and after is not None:
        block["problems"].extend(drift_violations(before, after))

    block["status"] = "VERIFIED" if not block["problems"] else "NOT_VERIFIED"
    block["summary"] = (
        f"{primary.summary_line()} — {'VERIFIED' if block['status'] == 'VERIFIED' else 'NOT VERIFIED'}"
    )
    return block


def require_release_evidence(env: dict[str, str] | None = None, *, context: str = "") -> None:
    """Refuse to produce release evidence from a mock run.

    Mechanical, not a comment: callers that intend to publish evidence call
    this and let it raise.  The check is on the same env var the fixtures read
    (tests/conftest.py ``IS_MOCK_MODE``), so a run cannot be mock for the
    fixtures and real for the evidence.
    """
    env = env if env is not None else dict(os.environ)
    if env.get(MOCK_ENV, "").strip().lower() in ("1", "true", "yes"):
        where = f" ({context})" if context else ""
        raise NdsIdentityError(
            f"{MOCK_ENV}=1{where}: refusing to emit release evidence from a mock router. "
            "A mock has no installed nodogsplash, so any version/hash it reported would be "
            "fabricated. Re-run against a real router, or drop the release-evidence requirement "
            "for this development run."
        )
