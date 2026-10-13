// SPDX-License-Identifier: Apache-2.0
//! Synthetic authorization/key providers, confined to the disposable test VM.
use super::*;
use async_trait::async_trait;
use base64::{engine::general_purpose::URL_SAFE_NO_PAD as B64, Engine};
use confidential_storage_guest::{
    agent_lifecycle::{Agent, Phase},
    lifecycle::{BackendError, BackendResult},
    linux_backend::{ConsumerHandles, LinuxBackend, Providers, Resolved, Secret},
    profile::{profile_sha256, HEADER_BYTES},
    receipt::{Claims, Expected, VerifiedGrant, Verifier},
};
use ring::{
    rand::SystemRandom,
    signature::{Ed25519KeyPair, KeyPair},
};
use std::{
    fs::{File, OpenOptions},
    io::Read,
    os::{
        fd::AsRawFd,
        unix::fs::{FileExt, MetadataExt},
    },
    sync::{Arc, Mutex},
    time::{SystemTime, UNIX_EPOCH},
};

#[derive(Default)]
struct Trace {
    events: Vec<&'static str>,
    handles: Option<ConsumerHandles>,
    keys: usize,
    releases: usize,
    quiesced: bool,
    released: bool,
}
#[derive(Clone)]
struct LabProviders {
    manifest: Vec<u8>,
    name: String,
    trace: Arc<Mutex<Trace>>,
}
fn mapped() -> bool {
    let out = Command::new("/usr/sbin/dmsetup")
        .args(["info", "-c", "--noheadings", "-o", "name"])
        .output()
        .unwrap();
    assert!(out.status.success());
    String::from_utf8(out.stdout).unwrap().contains("coco-")
}
fn stopped(name: &str) -> bool {
    fs::read_to_string(format!("/sys/fs/cgroup/callback-{name}/cgroup.events"))
        .unwrap()
        .contains("populated 0")
}
impl Providers for LabProviders {
    fn resolve(&self, _: &VerifiedGrant, device_id: &str) -> Result<Resolved, BackendError> {
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .open("/dev/vda")
            .unwrap();
        let stat = file.metadata().unwrap();
        let (major, minor) = (libc::major(stat.rdev()), libc::minor(stat.rdev()));
        assert_eq!(device_id, format!("{major}:{minor}"));
        let mut t = self.trace.lock().unwrap();
        assert!(t.events.contains(&"prepared"));
        t.events.push("resolve");
        Ok(Resolved {
            file,
            major,
            minor,
            manifest: self.manifest.clone(),
        })
    }
    fn key(&self, _: &VerifiedGrant, uri: &str) -> Result<Secret, BackendError> {
        assert_eq!(uri, "kbs:///storage-test/keys/scratch-v1");
        let mut t = self.trace.lock().unwrap();
        t.keys += 1;
        t.events.push("key");
        let mut key = [0u8; 32];
        File::open("/test-key")
            .unwrap()
            .read_exact(&mut key)
            .unwrap();
        if self.name == "encrypted-wrong-key" {
            key[0] ^= 1;
        }
        let secret = Secret::new(key);
        key.fill(0);
        Ok(secret)
    }
    fn quiesce_consumers(&self, _: &VerifiedGrant) -> BackendResult {
        if !self.trace.lock().unwrap().quiesced || !stopped(&self.name) {
            return Err(BackendError("lab consumer still active"));
        }
        self.trace.lock().unwrap().events.push("detach-authorized");
        Ok(())
    }
    fn release(&self, g: &VerifiedGrant) -> BackendResult {
        let mut t = self.trace.lock().unwrap();
        // Failed opens can try release while init is still paused. Force the
        // coordinator to quiesce it before this lab acknowledges any release.
        if !t.quiesced {
            return Err(BackendError("lab runtime must quiesce before release"));
        }
        assert!(stopped(&self.name));
        assert!(!mapped());
        assert_eq!(
            fs::read_dir("/proc/self/fd")
                .unwrap()
                .filter_map(Result::ok)
                .filter_map(|e| fs::read_link(e.path()).ok())
                .filter(|p| p == Path::new("/dev/vda"))
                .count(),
            0,
            "raw device descriptor retained at release"
        );
        assert!(!Path::new(&format!("/run/coco/storage/{}", g.claims().activation_id)).exists());
        if let Some(handles) = &t.handles {
            assert!(!Path::new(&format!(
                "/proc/self/fd/{}/data/kata-probe",
                handles.root.as_raw_fd()
            ))
            .exists());
        }
        t.releases += 1;
        t.events.push("release-attempt");
        if self.name == "encrypted-release-retry" && t.releases == 1 {
            return Err(BackendError("lab lost release acknowledgement"));
        }
        t.released = true;
        t.events.push("released");
        Ok(())
    }
}
struct ObservedRuntime {
    inner: KataRuntime,
    trace: Arc<Mutex<Trace>>,
}
#[async_trait]
impl Runtime for ObservedRuntime {
    type Consumer = ConsumerHandles;
    async fn prepare(&self, id: &Identity) -> Result<ConsumerHandles, BackendError> {
        assert!(!mapped());
        assert_eq!(fs::read_dir("/run/coco/storage").unwrap().count(), 0);
        let handles = self.inner.prepare(id).await?;
        let mut t = self.trace.lock().unwrap();
        t.handles = Some(ConsumerHandles {
            namespace: handles.namespace.try_clone().unwrap(),
            root: handles.root.try_clone().unwrap(),
            container_name: handles.container_name.clone(),
        });
        t.events.push("prepared");
        Ok(handles)
    }
    async fn start(&self, id: &Identity) -> BackendResult {
        assert!(mapped());
        assert_eq!(self.trace.lock().unwrap().keys, 1);
        self.inner.start(id).await?;
        self.trace.lock().unwrap().events.push("started");
        Ok(())
    }
    async fn quiesce(&self, id: &Identity) -> BackendResult {
        self.inner.quiesce(id).await?;
        assert!(stopped(&id.container_name));
        let mut t = self.trace.lock().unwrap();
        t.quiesced = true;
        t.events.push("quiesced");
        Ok(())
    }
    async fn destroy(&self, id: &Identity) -> BackendResult {
        assert!(self.trace.lock().unwrap().released);
        assert!(!mapped());
        assert_eq!(fs::read_dir("/run/coco/storage").unwrap().count(), 0);
        reap();
        self.inner.destroy(id).await?;
        self.trace.lock().unwrap().events.push("destroyed");
        Ok(())
    }
}
fn sha(bytes: &[u8]) -> String {
    ring::digest::digest(&ring::digest::SHA256, bytes)
        .as_ref()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}
fn grant(header: &[u8], name: &str) -> (VerifiedGrant, Vec<u8>) {
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../confidential-storage-guest/tests/receipt-fixture.json"
    ))
    .unwrap();
    let mut claims: Claims = serde_json::from_slice(
        &B64.decode(
            fixture["valid"]
                .as_str()
                .unwrap()
                .split('.')
                .nth(1)
                .unwrap(),
        )
        .unwrap(),
    )
    .unwrap();
    let manifest=serde_json::to_vec(&serde_json::json!({"schemaVersion":1,"volumeId":claims.volume_id,"generation":claims.generation,"profileSha256":profile_sha256(),"credentialUri":"kbs:///storage-test/keys/scratch-v1","luksUuid":"01234567-89ab-4cde-8123-456789abcdef","deviceBytes":134217728,"headerSha256":sha(header),"headerBytes":HEADER_BYTES,"dataOffsetBytes":HEADER_BYTES,"filesystem":"ext4"})).unwrap();
    claims.profile_sha256 = profile_sha256();
    claims.header_sha256 = sha(header);
    claims.manifest_sha256 = sha(&manifest);
    claims.credential_uri = "kbs:///storage-test/keys/scratch-v1".into();
    claims.iat = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs();
    claims.exp = claims.iat + 60;
    claims.container_name = name.into();
    claims.activation_id = format!("callback-{name}");
    let mut expected: Expected = serde_json::from_value(fixture["expected"].clone()).unwrap();
    expected.profile_sha256 = profile_sha256();
    expected.container_name = name.into();
    let key = Ed25519KeyPair::from_pkcs8(
        Ed25519KeyPair::generate_pkcs8(&SystemRandom::new())
            .unwrap()
            .as_ref(),
    )
    .unwrap();
    let h = serde_json::json!({"alg":"Ed25519","typ":"confidential-storage-grant+jwt","kid":"lab"});
    let signed = format!(
        "{}.{}",
        B64.encode(serde_json::to_vec(&h).unwrap()),
        B64.encode(serde_json::to_vec(&claims).unwrap())
    );
    let token = format!(
        "{signed}.{}",
        B64.encode(key.sign(signed.as_bytes()).as_ref())
    );
    (
        Verifier::new(
            key.public_key().as_ref().try_into().unwrap(),
            claims.iss,
            "lab".into(),
        )
        .verify(&token, &expected, claims.iat)
        .unwrap(),
        manifest,
    )
}
pub async fn test(mode: &str) {
    assert_eq!(
        fs::read_to_string("/sys/block/vda/size").unwrap().trim(),
        "262144"
    );
    fs::create_dir_all("/run/coco/storage").unwrap();
    fs::set_permissions("/run/coco/storage", fs::Permissions::from_mode(0o700)).unwrap();
    let mut header = vec![0; HEADER_BYTES];
    File::open("/dev/vda")
        .unwrap()
        .read_exact_at(&mut header, 0)
        .unwrap();
    let stat = fs::metadata("/dev/vda").unwrap();
    let device = format!("{}:{}", libc::major(stat.rdev()), libc::minor(stat.rdev()));
    let scenarios = if mode == "storage-reopen" {
        vec!["encrypted-after-reboot"]
    } else {
        vec![
            "encrypted-write",
            "encrypted-read",
            "encrypted-busy",
            "encrypted-release-retry",
            "encrypted-wrong-key",
            "encrypted-bad-manifest",
        ]
    };
    for name in scenarios {
        let write = if name == "encrypted-write" {
            "printf 'kata-encrypted-persistent-v1\\n' > /data/kata-probe; sync -f /data/kata-probe"
        } else {
            ":"
        };
        let script = format!(
            r#"set -eu
[ "$(id -u)" = 1000 ] && [ "$(id -g)" = 1000 ]
for cap in CapInh CapPrm CapEff CapBnd CapAmb; do grep -Eq "^$cap:[[:space:]]+0000000000000000$" /proc/self/status; done
grep -Eq '^NoNewPrivs:[[:space:]]+1$' /proc/self/status
[ ! -e /dev/vda ] && [ ! -e /dev/mapper ] && [ ! -e /test-key ] && [ ! -e /run/coco/storage ]
cp /bin/busybox /data/noexec-test; chmod 755 /data/noexec-test
if /data/noexec-test true 2>/dev/null; then exit 1; fi
rm /data/noexec-test
{write}
[ "$(cat /data/kata-probe)" = kata-encrypted-persistent-v1 ]
sha256sum /data/kata-probe > /result/hash
cat /proc/self/mountinfo > /result/mountinfo
cat /proc/self/status > /result/status
printf 'encrypted-app-ok\n' > /result/started
exec sleep 600
"#
        );
        let (inner, id, root) = setup_with_script(name, Some(&script));
        let (g, mut manifest) = grant(&header, name);
        assert_eq!(id.activation_id, g.claims().activation_id);
        assert_eq!(id.container_name, g.claims().container_name);
        if name == "encrypted-bad-manifest" {
            manifest[0] ^= 1;
        }
        let trace = Arc::new(Mutex::new(Trace::default()));
        let providers = LabProviders {
            manifest,
            name: name.into(),
            trace: trace.clone(),
        };
        let agent = Agent::new(
            LinuxBackend::new(providers).unwrap(),
            ObservedRuntime {
                inner,
                trace: trace.clone(),
            },
            g.claims().session_sha256.clone(),
            8,
        );
        assert!(agent.start(name).await.is_err());
        let prepared = agent.prepare(name.into(), g.clone(), device.clone()).await;
        if name == "encrypted-wrong-key" || name == "encrypted-bad-manifest" {
            assert!(prepared.is_err());
            agent.close(name).await.unwrap();
            assert!(!root.join("result/started").exists());
            assert_eq!(
                trace.lock().unwrap().keys,
                if name == "encrypted-wrong-key" { 1 } else { 0 }
            );
        } else {
            prepared.unwrap();
            assert_eq!(agent.phase(name).await.unwrap(), Phase::Ready);
            assert!(!root.join("result/started").exists());
            agent
                .prepare(name.into(), g.clone(), device.clone())
                .await
                .unwrap();
            agent.start(name).await.unwrap();
            agent.start(name).await.unwrap();
            wait_marker(&root.join("result/started")).await;
            assert_eq!(
                fs::read_to_string(root.join("result/hash"))
                    .unwrap()
                    .split_whitespace()
                    .next()
                    .unwrap(),
                sha(b"kata-encrypted-persistent-v1\n")
            );
            let mounts = fs::read_to_string(root.join("result/mountinfo")).unwrap();
            let ext4: Vec<_> = mounts.lines().filter(|l| l.contains(" - ext4 ")).collect();
            assert_eq!(ext4.len(), 1);
            assert_eq!(ext4[0].split_whitespace().nth(4), Some("/data"));
            for flag in ["rw", "nosuid", "nodev", "noexec"] {
                assert!(ext4[0]
                    .split_whitespace()
                    .nth(5)
                    .unwrap()
                    .split(',')
                    .any(|s| s == flag));
            }
            println!(
                "APP:{name}:uid=1000 gid=1000 caps=0 nnp=1 one-private-ext4=/data hash={}",
                sha(b"kata-encrypted-persistent-v1\n")
            );
            let busy = if name == "encrypted-busy" {
                let t = trace.lock().unwrap();
                Some(
                    File::open(format!(
                        "/proc/self/fd/{}/data/kata-probe",
                        t.handles.as_ref().unwrap().root.as_raw_fd()
                    ))
                    .unwrap(),
                )
            } else {
                None
            };
            if name == "encrypted-busy" || name == "encrypted-release-retry" {
                assert!(agent.close(name).await.is_err());
                assert_eq!(agent.phase(name).await.unwrap(), Phase::NeedsStorageClose);
                assert!(stopped(name));
                assert!(Path::new(&format!("/run/callback-runtime/{name}")).exists());
                assert!(agent.start(name).await.is_err());
                assert!(!trace.lock().unwrap().released);
                assert_eq!(mapped(), name == "encrypted-busy");
                drop(busy);
            }
            agent.close(name).await.unwrap();
        }
        agent.close(name).await.unwrap();
        assert_eq!(agent.phase(name).await.unwrap(), Phase::Closed);
        assert!(agent.start(name).await.is_err());
        assert!(agent.prepare(name.into(), g, device.clone()).await.is_err());
        assert!(!mapped());
        assert!(!Path::new(&format!("/sys/fs/cgroup/callback-{name}")).exists());
        let t = trace.lock().unwrap();
        assert!(t.released);
        let expected_starts =
            usize::from(name != "encrypted-wrong-key" && name != "encrypted-bad-manifest");
        assert_eq!(
            t.events.iter().filter(|e| **e == "started").count(),
            expected_starts
        );
        assert_eq!(t.events.iter().filter(|e| **e == "prepared").count(), 1);
        assert_eq!(
            t.releases,
            if name == "encrypted-release-retry" {
                2
            } else {
                1
            }
        );
        assert_eq!(t.events.last(), Some(&"destroyed"));
        println!("ORDER:{name}:{}", t.events.join(" -> "));
        println!("COCO_TEST_PASS:{name}");
    }
    println!("COCO_TEST_PASS:rustjail-encrypted-{mode}");
}
