#!/usr/bin/env bash
# PRTA-VM-LAB-LOCAL evidence: install the published tollgate-wrt apk into the
# running 25.12.5 VM and show the package is present.
set -euo pipefail
APK=~/tollgate-virtual-lab/artifacts/tollgate-wrt_0.6.0_alpha4_pre21_x86_64.apk
VM=root@10.99.99.1
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=10"

echo "=== VM identity ==="
ssh $SSH_OPTS $VM 'cat /etc/openwrt_version; ubus call system board | grep -E "release|description|target" || true'

echo "=== uploading published artifact ==="
ls -l "$APK"
scp -O $SSH_OPTS "$APK" $VM:/tmp/tollgate-wrt.apk

echo "=== apk add (published artifact) ==="
ssh $SSH_OPTS $VM 'apk add --allow-untrusted /tmp/tollgate-wrt.apk'

echo "=== installed package evidence ==="
ssh $SSH_OPTS $VM 'apk info tollgate-wrt 2>/dev/null | head -8; echo ---; ls -l /usr/bin/tollgate-wrt 2>/dev/null || find / -name "tollgate-wrt*" -not -path "/proc/*" 2>/dev/null | head -5'
