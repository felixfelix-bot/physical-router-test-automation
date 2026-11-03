#!/usr/bin/env python3
"""Mint one Cashu token from the lab's fakewallet mint (host side).

Kept separate from the POST so the token can be produced where the mint is
reachable (the host) and spent where the ROUTER can see the payer (inside the
lab client namespace). See purchase-client-e2e.py for why that split matters.
"""
from __future__ import annotations

import os
import sys

MINT = os.environ.get("LAB_MINT", "http://10.99.99.2:8383")
AMOUNT = int(os.environ.get("LAB_PURCHASE_SATS", "4"))


def main() -> int:
    try:
        from cashu.wallet.wallet import Wallet
    except Exception as e:  # pragma: no cover - environment dependent
        print(f"FAIL: cashu python wallet unavailable: {e}", file=sys.stderr)
        return 11

    import asyncio

    async def run() -> str:
        w = await Wallet.with_db(
            url=MINT,
            db="/home/c08r4d0r/.hermes/kanban/boards/tollgate-pwa-wallet/workspaces/t_94013caf/lab-wan/mint/wallet.db",
            name="lab-wallet",
        )
        await w.load_mint()
        # cashu 0.21 splits minting into request_mint -> (fakewallet pays
        # instantly) -> mint(amount, quote_id). There is no one-shot mint(n).
        quote = await w.request_mint(AMOUNT)
        proofs = await w.mint(AMOUNT, quote.quote)
        token = await w.serialize_proofs(proofs)
        return token

    try:
        token = asyncio.run(run())
    except Exception as e:
        print(f"FAIL: minting failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 11
    sys.stdout.write(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
