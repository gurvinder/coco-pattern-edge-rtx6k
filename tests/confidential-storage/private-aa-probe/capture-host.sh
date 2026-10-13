#!/bin/sh
set -eu
# Exact test initrd path as resolved by QEMU (usually /var/opt on RHCOS).
[ "$#" = 1 ]
case "$1" in /var/opt/kata-storage-test/private-aa-probe-*/initrd*) ;; *) exit 2 ;; esac
i=0
while [ "$i" -lt 50 ]; do
    sandbox=$(ps -C qemu-kvm -o args= | grep -F -- "-initrd $1 " | sed -n 's/.*-name sandbox-\([a-f0-9]\{64\}\).*/\1/p')
    if [ -n "$sandbox" ] && [ -S "/run/vc/vm/$sandbox/console.sock" ]; then
        timeout 85 socat -u "UNIX-CONNECT:/run/vc/vm/$sandbox/console.sock" -
        exit 0
    fi
    i=$((i + 1))
    sleep 1
done
exit 1
