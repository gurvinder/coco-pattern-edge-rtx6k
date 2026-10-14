#!/bin/sh
# Measured private service bootstrap; runs before application RPC admission.
set -eu
umask 077
ulimit -c 0
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export MKE2FS_CONFIG=/opt/coco-storage-tools/etc/mke2fs.conf
unset LD_PRELOAD LD_AUDIT GLIBC_TUNABLES LD_LIBRARY_PATH
[ "$(/opt/coco-storage-tools/bin/busybox id -u)" = 0 ]
[ "$(uname -r)" = 6.12.0-211.16.1.el10_2.x86_64 ]
[ ! -e /run/coco/storage-service ]
[ ! -e /run/coco/storage ]
modprobe dm_mod
modprobe dm_bufio
modprobe dm_crypt
modprobe dm_verity
modprobe ext4
for name in xor async_tx async_xor dm-integrity; do
    insmod "/opt/coco-storage-modules/$name.ko.xz"
done
mkdir -p /run/coco
chmod 0700 /run/coco
mkdir -m 0700 /run/coco/storage-service /run/coco/storage
mount -t tmpfs -o mode=0700,size=32m,nosuid,nodev,noexec tmpfs /run/coco/storage
mount --make-private /run/coco/storage
exec /usr/bin/confidential-storage-service
