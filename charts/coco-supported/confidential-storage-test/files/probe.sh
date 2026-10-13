#!/bin/sh
# Attachment diagnostic only: no keys, formatting, mounts or device writes.
set -eu
echo 'test=raw-block-delivery-only'
echo 'encrypted_storage_proven=false'
uname -r
id
grep -E '^(CapEff|NoNewPrivs|Seccomp):' /proc/self/status
for module in dm_crypt dm_integrity dm_verity; do
    if test -d "/sys/module/$module"; then
        echo "module_${module}=visible"
    else
        echo "module_${module}=not-visible"
    fi
done
if test -e /dev/mapper/control; then
    echo 'device_mapper_control=exposed'
    exit 1
fi
test -b /dev/storage-probe
stat -c 'device_type=%F mode=%a uid=%u gid=%g' /dev/storage-probe
# Read a single block into /dev/null. Never print or hash backing-store contents.
dd if=/dev/storage-probe of=/dev/null bs=4096 count=1
echo 'result=pass'
