set -eu
id
grep -E '^(CapEff|NoNewPrivs):' /proc/self/status
grep -Eq '^CapEff:[[:space:]]+0+$' /proc/self/status
grep -Eq '^NoNewPrivs:[[:space:]]+1$' /proc/self/status
test ! -e /dev/encrypted-data
test ! -e /dev/mapper/control
test ! -e /run/confidential-containers/cdh.sock
test "$(stat -c %u /data)" = 1000
test "$(stat -c %g /data)" = 1000
umask 077
cd /data
if test -e sentinel; then
    printf '%s\n' '__SENTINEL_SHA256__  sentinel' | sha256sum -c -
    case "$(cat reopen-count)" in 1|2) ;; *) exit 1;; esac
    printf '2\n' > reopen-count
    sync -f reopen-count
    sync -f /data
    echo STORAGE_RW_REOPEN_PASS
else
    test ! -e reopen-count
    printf '%s\n' 'confidential-storage-public-rw-persistence-canary-v1' > sentinel
    printf '1\n' > reopen-count
    sync -f sentinel
    sync -f reopen-count
    sync -f /data
    echo STORAGE_RW_INITIAL_WRITE_PASS
fi
printf '%s\n' '__SENTINEL_SHA256__  sentinel' | sha256sum -c -
echo STORAGE_RW_UNPRIVILEGED_PASS
sleep 120
