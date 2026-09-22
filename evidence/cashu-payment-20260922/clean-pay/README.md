# Release-Blocking Smoke Test — ONE REAL End-to-End Cashu Payment on Single-Hop Lab Merchant (`beta`)

Date: 2026-09-22
Router: GL-MT3000 (`beta`), 192.168.8.1, OpenWrt 25.12.5, tollgate-wrt v0.5.0 + nodogsplash 5.0.2
Test client: workstation at 192.168.8.2 (LAN), MAC 00:e0:4c:68:3d:2d
Tooling: kit `~/physical-router-test-automation` (HttpMinter in `lib/cashu.py` — existing mechanism)

## RESULT: PASS — session flipped false→true, real Cashu token redeemed and proved SPENT at mint.

Payment token: **21 sats**, mint `https://nofee.testnut.cashu.space`, keyset `00b4cd27d8861a44`.
(Full token secret NOT pasted — evidence only.)

---

## 1. Fund a REAL spendable testnut token + prove it (wallet proof, step 1)

Minted via the kit's existing HTTP minter (NUT-04 /v1/mint/bolt11 + FakeWallet auto-pay),
`lib/cashu.py::HttpMinter`.

```bash
PYTHONPATH=. python3 -c "from lib.cashu import HttpMinter; tok=HttpMinter('https://nofee.testnut.cashu.space').mint(21)"
# -> MINTED LEN 858
```

Proved real/spendable pre-payment via mint `/v1/checkstate`:

```
AMT 21 MINT https://nofee.testnut.cashu.space KEYSET {'00b4cd27d8861a44'}
states ['UNSPENT', 'UNSPENT', 'UNSPENT']
```

## 2. Drive purchase through the RUNNING stack (kit mechanism, NOT a new harness)

Kit documented mechanism = `lib/router.py::pay_direct`: POST raw token to backend `/` on :2121
with `Content-Type: text/plain` + `X-Forwarded-For`. Replicated exactly with curl against the
live merchant. (Portal route :2050 requires a real NDS captive-portal redirect; the backend
payment path is the same purchase the portal JS triggers.)

Client first registered in NDS as Preauthenticated via a WAN-bound probe (Product Bug 1 fix).

```bash
curl -s --max-time 30 -d "$TOK" \
  -H 'Content-Type: text/plain' \
  -H 'X-Forwarded-For: 192.168.8.2' \
  http://192.168.8.1:2121/
# -> {"kind":1022,... "allotment":462422016, "metric":"bytes", "start-time":1790066217}
```

## 3. SESSION FLIPPED — the release-blocking assertion (verbatim before/after)

**BEFORE** (`GET /balance`):
```json
{"status":1,"session_active":false,"usage":0,"allotment":0,"remaining":0}
```

**AFTER** (`GET /balance`):
```json
{"status":1,"session_active":true,"metric":"bytes","usage":0,"allotment":462422016,"remaining":462422016,"start_time":1790066217}
```

`GET /usage` moved: `-1/-1` → `1024/462422016`, and climbed live to ~805888 as data flows.

## 4. Token redeemed AND proved SPENT at mint (wallet drain proof)

Post-payment `GET /v1/checkstate` on the SAME token proofs:
```
POST-PAYMENT token states: ['SPENT', 'SPENT', 'SPENT']
ALL SPENT: True
```

On-router wallet.db evidence (sha256 + size):
- Clean baseline: `1d79255a4c...` (65536 B, before this purchase)
- After payment: `5cd52315dc...` (131072 B) — wallet grew exactly as the 21-sat proof set persisted.

`tollgate --json status` → `wallet_ok: true`.

## 5. Router log receipts (logread, immediate capture)

```
PurchaseSession: calling Receive for mint=https://nofee.testnut.cashu.space token_amount=21 mac=00:e0:4c:68:3d:2d
PurchaseSession: Receive completed, amount=21, err=<nil>
Amount after swap: 21
Converting 21 steps to 462422016 bytes using step size 22020096
Authorization successful for MAC attempts=1 mac_address="00:e0:4c:68:3d:2d" module=valve output="Client 00:e0:4c:68:3d:2d authenticated."
```

NSD client state after: `state: "Authenticated"`.

---

## Preconditions discovered (and resolved) to reach this PASS

1. **Merchant wallet was poisoned (Product Bug 2).** First token attempt failed:
   `could not swap proofs: outputs have already been signed before` — the on-router wallet.db
   held deterministic blinded outputs already signed by the mint from a prior run. Fixed per
   documented recovery: back up + `rm /etc/tollgate/wallet.db` + restart tollgate-wrt (fresh
   masterKey → mint never saw these outputs). This is the KNOWN tollgate-wrt-restart swap break.
2. **Test client was in NDS `trustedmac` list** (management keepalive armor), so `ndsctl auth`
   exited rc=1 → gate-open failed → session rolled back even after Redeem succeeded. Removed
   the trustedmac entry for this run and re-registered the client Preauthenticated via a WAN
   probe (Product Bug 1 fix). SSH-22 remained open via `users_to_router`.

## Evidence files (this directory)

- `portal-splash.png` — screenshot of the captive portal SPA (authenticated client render)
- `balance-before.json`, `balance-after.json`, `balance-after-final.json` — verbatim session JSON
- `payment-response.txt` — the kind:1022 session event JSON returned by the backend
- `router-wallet-before.txt` / `router-wallet-after.txt` — on-router wallet.db sha256/size
- `token-final.txt` — the minted token (SECRET, gitignored via `-f` skip or kept local)
- `TOKEN-META.txt` — amount / mint URL / keyset id (no secret)

## Replay command (reviewer can re-run)

Requires: router `beta` reachable at 192.168.8.1:2121, mint nofee.testnut.cashu.space reachable,
kit at ~/physical-router-test-automation with `python3 -c "import coincurve"` working.

```bash
cd ~/physical-router-test-automation
PYTHONPATH=. python3 - <<'PY'
import json, urllib.request
from lib.cashu import HttpMinter

# 1. mint 21-sat token (real spendable testnut ecash)
tok = HttpMinter('https://nofee.testnut.cashu.space').mint(21)

# 2. prove UNSPENT
def b64d(s): return base64.urlsafe_b64decode(s+'='*(-len(s)%4)).decode()
js = json.loads(b64d(tok[len('cashuA'):])); p0 = js['token'][0]
Ys = [HttpMinter._hash_to_curve(p['secret'].encode()).format().hex() for p in p0['proofs']]
req = urllib.request.Request('https://nofee.testnut.cashu.space/v1/checkstate',
    data=json.dumps({'Ys':Ys}).encode(), headers={'Content-Type':'application/json'})
st = json.load(urllib.request.urlopen(req))
print('pre states', [x['state'] for x in st['states']])

# 3. pay via kit pay_direct mechanism
import subprocess
before = subprocess.run(['curl','-s','http://192.168.8.1:2121/balance'],capture_output=True,text=True).stdout
subprocess.run(['curl','-s','--max-time','30','-d',tok,'-H','Content-Type: text/plain',
    '-H','X-Forwarded-For: 192.168.8.2','http://192.168.8.1:2121/'],capture_output=True,text=True)
after = subprocess.run(['curl','-s','http://192.168.8.1:2121/balance'],capture_output=True,text=True).stdout
print('BEFORE', before.strip())
print('AFTER ', after.strip())
assert '"session_active":true' in after, 'SESSION DID NOT FLIP'

# 4. prove SPENT
st2 = json.load(urllib.request.urlopen(req))
print('post states', [x['state'] for x in st2['states']])
PY
```
