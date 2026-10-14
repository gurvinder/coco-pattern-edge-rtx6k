#!/bin/sh
# No PVC/KBS: fresh 1 GiB disk on guest-private tmpfs only.
set -eu
umask 077
ulimit -c 0
bb=/opt/coco-storage-tools/bin/busybox
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
sleep 20
failed() {
    echo NATIVE_INITIALIZER_PROBE_FAILED
    # Disposable RAM disk only. The integrity table and cryptsetup status never
    # contain key bytes; never dump a dm-crypt table or use --showkeys.
    name=coco-init-af000000-1111-4222-8333-444444444444
    /usr/sbin/dmsetup table --target integrity "${name}_dif" || true
    /usr/sbin/cryptsetup status "$name" || true
}
trap failed EXIT
[ "$(uname -r)" = 6.12.0-211.16.1.el10_2.x86_64 ]
modprobe dm_mod
modprobe dm_bufio
modprobe dm_crypt
modprobe ext4
modprobe loop
for name in xor async_tx async_xor dm-integrity; do
    insmod "/opt/coco-storage-modules/$name.ko.xz"
done
work=/run/storage-initializer-probe
[ ! -e "$work" ]
mkdir -m 0700 "$work"
mount -t tmpfs -o size=1152m,mode=0700,nosuid,nodev,noexec tmpfs "$work"
"$bb" truncate -s 1G "$work/disk"
loop=$("$bb" losetup -f)
"$bb" losetup "$loop" "$work/disk"
/usr/bin/storage-initializer-probe "$loop"
"$bb" losetup -d "$loop"
"$bb" umount "$work"
"$bb" rmdir "$work"
trap - EXIT
echo NATIVE_INITIALIZER_PROBE_CLEANUP_PASS
