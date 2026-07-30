#!/usr/bin/env python3
"""Recover stuck Cashu tokens from tollgate-wrt tokens-to-recover.txt.

Usage:
    # Check which tokens are still unspent (no changes)
    python scripts/recover-tokens.py --file tokens-to-recover.txt --check

    # Check + re-submit unspent tokens to router
    python scripts/recover-tokens.py --file tokens-to-recover.txt --recover

    # Dry run (parse + show what would happen, no network calls)
    python scripts/recover-tokens.py --file tokens-to-recover.txt --dry-run

Token file format (pipe-delimited, one per line):
    2026-06-18T16:14:05Z | https://nofee.testnut.cashu.space | cashuBo2F0... | payment rejected: failed to open gate: exit status 1
"""

import argparse
import base64
import json
import sys
import time
from pathlib import Path

import requests

# NUT-07 checkstate endpoint
CHECKSTATE_PATH = "/v1/checkstate"


def parse_token_file(filepath):
    """Parse tokens-to-recover.txt into list of token records.

    Each line: timestamp | mint_url | cashuB_token | rejection_error
    """
    records = []
    text = Path(filepath).read_text()
    for line in text.strip().split("\n"):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(" | ")]
        if len(parts) < 3:
            continue
        records.append({
            "timestamp": parts[0],
            "mint_url": parts[1],
            "token": parts[2],
            "error": parts[3] if len(parts) > 3 else "",
        })
    return records


def decode_cashu_token(token):
    """Decode a Cashu V3 (cashuB...) or V4 (cashuA...) token.

    Returns the decoded JSON structure with proofs/token/mint info.
    """
    import zlib

    for prefix, encoder in [("cashuB", "cbor"), ("cashuA", "json")]:
        if token.startswith(prefix):
            raw = token[len(prefix):]
            # base64url decode
            padded = raw + "=" * (4 - len(raw) % 4)
            data = base64.urlsafe_b64decode(padded)

            # Try decompress (Cashu V4 uses gzip)
            try:
                data = zlib.decompress(data, wbits=zlib.MAX_WBITS | 16)
            except Exception:
                pass  # not compressed

            if encoder == "json":
                return json.loads(data)
            else:
                # CBOR decode
                try:
                    import cbor2
                    return cbor2.loads(data)
                except ImportError:
                    # Fallback: try treating as JSON after decompress
                    try:
                        return json.loads(data)
                    except Exception:
                        raise ValueError("Cannot decode CBOR token without cbor2 library")

    raise ValueError(f"Unknown token format: {token[:20]}...")


def extract_proofs(decoded):
    """Extract proof list from decoded token structure.

    Handles V3 CBOR format: {"t": [{"i": keyset_id, "p": [...]}], "m": mint_url, "u": "sat"}
    Handles V3 JSON format: {"token": [{"mint": ..., "proofs": [...]}], "unit": "sat"}
    Handles V4 JSON format: {"token": {"mint": ..., "proofs": [...]}, ...}
    """
    # CBOR short-key format: {"t": [...], "m": ..., "u": ...}
    if isinstance(decoded, dict):
        if "t" in decoded:
            token_list = decoded["t"]
            if isinstance(token_list, list):
                proofs = []
                for entry in token_list:
                    proofs.extend(entry.get("p", []))
                return proofs
        # JSON nested token
        token_obj = decoded.get("token", decoded)
        if isinstance(token_obj, dict):
            return token_obj.get("proofs", [])
        elif isinstance(token_obj, list):
            proofs = []
            for entry in token_obj:
                proofs.extend(entry.get("proofs", []))
            return proofs
    elif isinstance(decoded, list):
        return decoded
    return []


def extract_mint_url(decoded):
    """Extract mint URL from decoded token if present."""
    if isinstance(decoded, dict):
        token_obj = decoded.get("token", decoded)
        if isinstance(token_obj, dict):
            return token_obj.get("mint", "")
        elif isinstance(token_obj, list) and token_obj:
            return token_obj[0].get("mint", "")
    return ""


def check_token_state(mint_url, token, timeout=10):
    """Check if token's proofs are spent at the mint (NUT-07 checkstate).

    Returns dict with 'unspent_count', 'spent_count', 'total_proofs'.
    """
    decoded = decode_cashu_token(token)
    proofs = extract_proofs(decoded)

    if not proofs:
        return {"unspent_count": 0, "spent_count": 0, "total_proofs": 0, "error": "no proofs found"}

    # Build checkstate request — NUT-07 uses Y values
    check_proofs = []
    for p in proofs:
        # CBOR format uses 'c' for commitment/Y, JSON uses 'Y'
        y_val = ""
        if "c" in p:
            # CBOR bytes — convert to hex
            c = p["c"]
            if isinstance(c, bytes):
                y_val = c.hex()
            else:
                y_val = str(c)
        elif "Y" in p:
            y_val = p["Y"]
        if y_val:
            check_proofs.append({"Y": y_val})

    resp = requests.post(
        f"{mint_url}{CHECKSTATE_PATH}",
        json={"proofs": check_proofs},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()

    states = data.get("states", [])
    unspent = sum(1 for s in states if s.get("state") == "UNSPENT")
    spent = sum(1 for s in states if s.get("state") == "SPENT")

    return {
        "unspent_count": unspent,
        "spent_count": spent,
        "total_proofs": len(states),
    }


def submit_token_to_router(router_ip, token, timeout=30):
    """Re-submit a Cashu token to the router's API for processing.

    The router API on :2121 accepts the raw token string as POST body
    (not JSON-wrapped) — same format the captive portal uses.
    """
    resp = requests.post(
        f"http://{router_ip}:2121/",
        data=token,
        headers={"Content-Type": "text/plain"},
        timeout=timeout,
    )
    return resp.status_code, resp.text


def get_token_amount(decoded):
    """Sum all proof amounts from decoded token.

    Handles CBOR short keys (a=amount) and JSON keys (amount=amount).
    """
    proofs = extract_proofs(decoded)
    return sum(p.get("a", p.get("amount", 0)) for p in proofs)


def main():
    parser = argparse.ArgumentParser(
        description="Recover stuck Cashu tokens from tokens-to-recover.txt"
    )
    parser.add_argument("--file", required=True, help="Path to tokens-to-recover.txt")
    parser.add_argument("--router", default="192.168.8.1", help="Router IP (default: 192.168.8.1)")
    parser.add_argument("--check", action="store_true", help="Check token states at mint only")
    parser.add_argument("--recover", action="store_true", help="Re-submit unspent tokens to router")
    parser.add_argument("--dry-run", action="store_true", help="Parse and show what would happen")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between tokens in seconds")
    args = parser.parse_args()

    if not args.check and not args.recover and not args.dry_run:
        parser.print_help()
        sys.exit(1)

    records = parse_token_file(args.file)
    print(f"Found {len(records)} tokens to process\n")

    stats = {"total": len(records), "unspent": 0, "spent": 0, "recovered": 0,
             "failed": 0, "skipped": 0}
    results = []

    for i, record in enumerate(records):
        token_preview = record["token"][:50] + "..."
        print(f"[{i+1}/{len(records)}] {record['timestamp']}")
        print(f"  Mint: {record['mint_url']}")
        print(f"  Token: {token_preview}")
        print(f"  Original error: {record['error']}")

        if args.dry_run:
            try:
                decoded = decode_cashu_token(record["token"])
                amount = get_token_amount(decoded)
                print(f"  Amount: {amount} sats")
                stats["unspent" if amount > 0 else "spent"] += 1
            except Exception as e:
                print(f"  PARSE ERROR: {e}")
                stats["failed"] += 1
            results.append({"index": i, **record, "action": "dry-run"})
            print()
            continue

        # Check state at mint
        try:
            state = check_token_state(record["mint_url"], record["token"])
            print(f"  State: {state['unspent_count']} unspent / {state['spent_count']} spent "
                  f"(of {state['total_proofs']} proofs)")
        except Exception as e:
            print(f"  STATE CHECK FAILED: {e}")
            state = None
            stats["failed"] += 1

        if state and state["unspent_count"] > 0:
            stats["unspent"] += 1
            if args.recover:
                print(f"  ACTION: Re-submitting unspent token to router...")
                try:
                    status, body = submit_token_to_router(args.router, record["token"])
                    print(f"  RESULT: HTTP {status}")
                    if status == 200:
                        stats["recovered"] += 1
                        print(f"  ✓ RECOVERED")
                    elif status == 400:
                        # Could be NDS exit 1 bug — check if "failed to open gate"
                        if "failed to open gate" in body:
                            print(f"  ⚠ Token consumed but NDS gate-open failed (known bug)")
                            stats["recovered"] += 1
                        else:
                            print(f"  ✗ REJECTED: {body[:100]}")
                            stats["failed"] += 1
                    else:
                        print(f"  ✗ ERROR: {body[:100]}")
                        stats["failed"] += 1
                except Exception as e:
                    print(f"  SUBMIT FAILED: {e}")
                    stats["failed"] += 1
        elif state and state["unspent_count"] == 0:
            stats["spent"] += 1
            print(f"  SKIP: all proofs already SPENT at mint")
        else:
            stats["skipped"] += 1

        results.append({"index": i, **record, "state": state})

        if args.delay and i < len(records) - 1:
            time.sleep(args.delay)
        print()

    # Summary
    print("=" * 50)
    print(f"SUMMARY")
    print(f"  Total tokens:  {stats['total']}")
    print(f"  Unspent:       {stats['unspent']}")
    print(f"  Spent (skip):  {stats['spent']}")
    print(f"  Recovered:     {stats['recovered']}")
    print(f"  Failed:        {stats['failed']}")
    print(f"  Skipped:       {stats['skipped']}")
    print("=" * 50)

    # Write detailed results
    results_path = Path(args.file).parent / "recovery-results.json"
    Path(results_path).write_text(json.dumps(results, indent=2, default=str))
    print(f"Detailed results: {results_path}")


if __name__ == "__main__":
    main()
