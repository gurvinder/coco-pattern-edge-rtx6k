#!/bin/sh
# Node-side activation of the already staged GPU candidate in the idle TEST
# handler. Preflight all cluster Pods first: no nonterminal pod may use that
# handler or an alias. Never run against the production GPU configuration.
# Retains a byte-for-byte backup; does not restart/reload CRI-O or delete pods.
set -eu
[ "$(id -u)" = 0 ]
work=/opt/kata-storage-test/gpu-candidate-c47a03674c2b
target=/opt/kata-storage-test/private-aa-probe-94354456b0c3/configuration.toml
production=/etc/kata-containers/kata-snp-nvidia-gpu/configuration.toml
[ ! -L "$target" ]
[ ! -e "$work/previous-storage-runtime.toml" ]
[ ! -e "$target.gpu-candidate.tmp" ]
printf '%s  %s\n' \
  a7e8c9bfd9ddab3645c1655f1e0c19473ccd3c8d89da8942ec8057dc33768af6 "$target" \
  0605f273d60d1b7e18aa5d37367740f073da2e84208b2082fcc02d8df0ac5898 "$production" \
  d982ebd9493c7f0dc6eb7382aee3ef6a232b14c4a7ea1cc232315cc2d18c768e "$work/configuration.toml" \
  c47a03674c2bc1119aa0077b0f181c03d1044be958137dbd519a303e2709fe1c "$work/initrd" | sha256sum -c -
crio_identity() {
    found=
    for entry in /proc/[0-9]*/comm; do
        [ -r "$entry" ] || continue
        read -r process < "$entry" || continue
        if [ "$process" = crio ]; then
            [ -z "$found" ] || return 1
            pid=${entry#/proc/}
            pid=${pid%/comm}
            started=$(awk '{print $22}' "/proc/$pid/stat")
            [ -n "$started" ] || return 1
            found="$pid:$started"
        fi
    done
    [ -n "$found" ] || return 1
    printf '%s' "$found"
}
before=$(crio_identity)
cp -p "$target" "$work/previous-storage-runtime.toml"
cp "$work/configuration.toml" "$target.gpu-candidate.tmp"
chmod 0644 "$target.gpu-candidate.tmp"
chcon --reference="$target" "$target.gpu-candidate.tmp"
mv -T "$target.gpu-candidate.tmp" "$target"
[ "$before" = "$(crio_identity)" ]
printf '%s  %s\n' \
  a7e8c9bfd9ddab3645c1655f1e0c19473ccd3c8d89da8942ec8057dc33768af6 "$work/previous-storage-runtime.toml" \
  0605f273d60d1b7e18aa5d37367740f073da2e84208b2082fcc02d8df0ac5898 "$production" \
  d982ebd9493c7f0dc6eb7382aee3ef6a232b14c4a7ea1cc232315cc2d18c768e "$target" | sha256sum -c -
printf 'CRIO_PID_AND_STARTTIME_UNCHANGED=%s\n' "$before"
