#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Userspace-only test: never opens a device-mapper target or touches a real disk.
set -euo pipefail
umask 077
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
cryptsetup_bin=${COCO_TEST_CRYPTSETUP:-cryptsetup}
test_dir=$(mktemp -d "${TMPDIR:-/tmp}/coco-header-test.XXXXXXXX")
trap 'rm -rf -- "$test_dir"' EXIT
# Disposable random key and regular sparse file, both removed on exit.
dd if=/dev/urandom of="$test_dir/key" bs=32 count=1 status=none
truncate -s 64M "$test_dir/plain-luks.img"
"$cryptsetup_bin" --version
"$cryptsetup_bin" luksFormat --type luks2 --batch-mode --disable-locks \
    --cipher aes-xts-plain64 --key-size 512 --sector-size 4096 \
    --uuid 01234567-89ab-4cde-8123-456789abcdef \
    --pbkdf argon2id --pbkdf-memory 65536 --pbkdf-parallel 1 \
    --pbkdf-force-iterations 4 --luks2-metadata-size 16k \
    --luks2-keyslots-size 16744448 --offset 32768 \
    --key-file "$test_dir/key" "$test_dir/plain-luks.img"
# This is deliberately ordinary LUKS without authenticated sectors. Prove it is
# usable with its real key, then require our RW profile to reject its header.
"$cryptsetup_bin" open --type luks2 --test-passphrase --disable-locks \
    --key-file "$test_dir/key" "$test_dir/plain-luks.img"
dd if="$test_dir/plain-luks.img" of="$test_dir/header" bs=1M count=16 status=none
export COCO_TEST_CRYPTSETUP="$cryptsetup_bin"
export COCO_TEST_PLAIN_LUKS_HEADER="$test_dir/header"
cd "$repo_dir/components/confidential-storage-guest"
cargo test --locked --lib \
    profile::tests::cryptsetup_parses_sealed_synthetic_header_and_real_plain_luks_is_rejected \
    -- --ignored --exact
