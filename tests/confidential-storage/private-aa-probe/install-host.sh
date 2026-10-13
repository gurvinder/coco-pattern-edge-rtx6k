#!/bin/sh
# Diagnostic only. Explicit approval is pending for this shared-runtime change.
# Input: base64 of the exact no-secret probe image pinned below.
set -eu
[ "$(id -u)" = 0 ]
base=/etc/kata-containers/kata-snp/configuration.toml
source_dropin=/etc/crio/crio.conf.d/50-kata-snp
work=/opt/kata-storage-test/private-aa-probe-94354456b0c3
new_dropin=/etc/crio/crio.conf.d/99-kata-storage-probe
[ ! -e "$work" ]
[ ! -e "$new_dropin" ]
printf '%s  %s\n' eaaef2096a34cea8a2a945b48b0f1a9e66b429b31de7bed25a31ebe23d124f87 "$base" ece4c18280776ee3e191c159c8668a7e9f7d78eff39a8ec21029a224e7814394 "$source_dropin" | sha256sum -c -
install -d -m 0755 /opt/kata-storage-test
mkdir -m 0755 "$work"
base64 -d > "$work/initrd.partial"
printf '%s  %s\n' 94354456b0c3aff0704ff8eb0cbaf47023d6be66933fb2530d147b7e714a4678 "$work/initrd.partial" | sha256sum -c -
chmod 0644 "$work/initrd.partial"
mv "$work/initrd.partial" "$work/initrd"
sed 's|^initrd = .*|initrd = "/opt/kata-storage-test/private-aa-probe-94354456b0c3/initrd"|' "$base" > "$work/configuration.toml"
sed -e 's/\[crio.runtime.runtimes.kata-snp\]/[crio.runtime.runtimes.kata-snp-storage-probe]/' -e 's|/etc/kata-containers/kata-snp/configuration.toml|/opt/kata-storage-test/private-aa-probe-94354456b0c3/configuration.toml|' "$source_dropin" > "$work/crio.conf"
chmod 0644 "$work/configuration.toml" "$work/crio.conf"
restorecon -RF "$work"
install -m 0644 "$work/crio.conf" /etc/crio/crio.conf.d/.99-kata-storage-probe.tmp
ln /etc/crio/crio.conf.d/.99-kata-storage-probe.tmp "$new_dropin"
rm /etc/crio/crio.conf.d/.99-kata-storage-probe.tmp
restorecon "$new_dropin"
before=$(systemctl show crio -p MainPID --value)
systemctl reload crio
after=$(systemctl show crio -p MainPID --value)
[ "$before" = "$after" ]
printf 'CRIO_PID_UNCHANGED=%s\n' "$after"
printf '%s  %s\n' eaaef2096a34cea8a2a945b48b0f1a9e66b429b31de7bed25a31ebe23d124f87 "$base" ece4c18280776ee3e191c159c8668a7e9f7d78eff39a8ec21029a224e7814394 "$source_dropin" | sha256sum -c -
