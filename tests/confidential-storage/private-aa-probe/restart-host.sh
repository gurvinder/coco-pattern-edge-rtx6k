#!/bin/sh
# Run only after explicit approval of a shared CRI-O restart.
# No node reboot, pod deletion, runtime replacement or automatic retry.
set -eu
[ "$(id -u)" = 0 ]
[ "$(systemctl show crio -p ActiveState --value)" = active ]
printf '%s  %s\n' eaaef2096a34cea8a2a945b48b0f1a9e66b429b31de7bed25a31ebe23d124f87 /etc/kata-containers/kata-snp/configuration.toml ece4c18280776ee3e191c159c8668a7e9f7d78eff39a8ec21029a224e7814394 /etc/crio/crio.conf.d/50-kata-snp d949fefa0716061ea88e1afc7ee66cca1b92302022af60a2e2f5faea6287290e /opt/kata-storage-test/private-aa-probe-d949fefa0716/initrd | sha256sum -c -
before=$(systemctl show crio -p MainPID --value)
systemctl restart --no-block crio
i=0
while [ "$i" -lt 90 ]; do
    after=$(systemctl show crio -p MainPID --value)
    if [ "$after" != 0 ] && [ "$after" != "$before" ] && systemctl is-active --quiet crio; then
        printf 'CRIO_RESTARTED old_pid=%s new_pid=%s\n' "$before" "$after"
        exit 0
    fi
    i=$((i + 1))
    sleep 1
done
printf 'CRIO_RESTART_NOT_CONFIRMED; inspect service, do not retry automatically\n' >&2
exit 1
