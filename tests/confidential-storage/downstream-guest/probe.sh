#!/bin/sh
# Kernel compatibility probe only: random key and new RAM-backed disk, no PVC/KBS.
set -eu
umask 077
ulimit -c 0
bb=/opt/coco-storage-tools/bin/busybox
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export MKE2FS_CONFIG=/opt/coco-storage-tools/etc/mke2fs.conf
sleep 20
trap 'echo STORAGE_KERNEL_PROBE_FAILED' EXIT
[ "$(uname -r)" = 6.12.0-211.16.1.el10_2.x86_64 ]
modprobe dm_mod
modprobe dm_bufio
modprobe dm_crypt
modprobe dm_verity
modprobe ext4
modprobe loop
for name in xor async_tx async_xor dm-integrity; do
    insmod "/opt/coco-storage-modules/$name.ko.xz"
done
echo STORAGE_KERNEL_MODULES_PASS
work=/run/storage-kernel-probe
[ ! -e "$work" ]
mkdir -m 0700 "$work"
# A separate tmpfs makes it impossible for this probe to format persistent data.
mount -t tmpfs -o size=192m,mode=0700,nosuid,nodev,noexec tmpfs "$work"
"$bb" truncate -s 128M "$work/disk"
"$bb" dd if=/dev/urandom of="$work/key" bs=32 count=1 2>/dev/null
"$bb" dd if=/dev/urandom of="$work/wrong-key" bs=32 count=1 2>/dev/null
cryptsetup --version
cryptsetup luksFormat --batch-mode --type luks2 --cipher aes-xts-plain64 \
    --key-size 512 --integrity hmac-sha256 --sector-size 4096 \
    --pbkdf argon2id --pbkdf-memory 65536 --pbkdf-parallel 1 \
    --pbkdf-force-iterations 4 --luks2-metadata-size 16k \
    --luks2-keyslots-size 16744448 --offset 32768 \
    --key-file "$work/key" "$work/disk"
echo STORAGE_KERNEL_FORMAT_PASS
if cryptsetup open --type luks2 --disable-external-tokens --disable-keyring \
    --key-file "$work/wrong-key" "$work/disk" storage-kernel-probe; then
    echo STORAGE_KERNEL_WRONG_KEY_ACCEPTED
    exit 1
fi
echo STORAGE_KERNEL_WRONG_KEY_PASS
cryptsetup open --type luks2 --disable-external-tokens --disable-keyring \
    --key-file "$work/key" "$work/disk" storage-kernel-probe
# Inspect only the integrity target; never print a dm-crypt table or key.
dmsetup table storage-kernel-probe_dif | "$bb" grep -Eq '^0 [0-9]+ integrity [0-9]+:[0-9]+ [0-9]+ 32 J '
dmsetup table storage-kernel-probe_dif | "$bb" grep -Eq '(^| )block_size:4096( |$)'
echo STORAGE_KERNEL_JOURNALED_HMAC_PASS
mkfs.ext4 -q /dev/mapper/storage-kernel-probe
mkdir "$work/mount"
mount -t ext4 -o nosuid,nodev,noexec /dev/mapper/storage-kernel-probe "$work/mount"
printf '%s\n' 'guest-only-ram-disk-sentinel' > "$work/mount/sentinel"
"$bb" sync
"$bb" umount "$work/mount"
cryptsetup close storage-kernel-probe
cryptsetup open --type luks2 --disable-external-tokens --disable-keyring \
    --key-file "$work/key" "$work/disk" storage-kernel-probe
mount -t ext4 -o nosuid,nodev,noexec /dev/mapper/storage-kernel-probe "$work/mount"
[ "$(cat "$work/mount/sentinel")" = guest-only-ram-disk-sentinel ]
"$bb" umount "$work/mount"
cryptsetup close storage-kernel-probe
"$bb" umount "$work"
"$bb" rmdir "$work"
echo STORAGE_KERNEL_REOPEN_CLEANUP_PASS
trap - EXIT
echo STORAGE_KERNEL_PROBE_PASS
