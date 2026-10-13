// SPDX-License-Identifier: Apache-2.0
//! Lifecycle adapter for guest-private staging only. Trusted providers are
//! injected; there is no production resolver, attested CDH client or RPC here.
use crate::{
    guest_mount::{GuestMount, MountRoot},
    lifecycle::{Backend, BackendError, BackendResult},
    opener::{BlockDevice, Opener, Phase},
    receipt::VerifiedGrant,
};
use async_trait::async_trait;
use std::{
    collections::HashMap,
    fs::File,
    sync::{Arc, Mutex},
};

pub struct Resolved {
    pub file: File,
    pub major: u32,
    pub minor: u32,
    pub manifest: Vec<u8>,
}
/// Providers must run inside the trusted guest service. Neither an application
/// nor an untrusted host can implement this boundary in the deployed artifact.
pub trait Providers: Send + Sync + 'static {
    fn resolve(&self, grant: &VerifiedGrant, device_id: &str) -> Result<Resolved, BackendError>;
    /// Called only after exact manifest/header/profile authentication. A real
    /// implementation must bind CDH delivery to this attested session and grant.
    fn key(&self, grant: &VerifiedGrant, credential_uri: &str) -> Result<Secret, BackendError>;
    /// Idempotently remove every application bind for this activation, across
    /// retained namespaces. No lazy/forced unmount; failure must retain ownership.
    /// A production implementation belongs to the guest agent's bind manager.
    fn detach_consumers(&self, grant: &VerifiedGrant) -> BackendResult;
    /// Must be an idempotent acknowledgement on the attested authority channel.
    fn release(&self, grant: &VerifiedGrant) -> BackendResult;
}
/// No Debug/Clone. Wipes this owned buffer on drop; providers must also erase any
/// temporary copies. Service-wide dump/swap/tracing hardening is still required.
pub struct Secret([u8; 32]);
impl Secret {
    pub fn new(bytes: [u8; 32]) -> Self {
        Self(bytes)
    }
}
impl Drop for Secret {
    fn drop(&mut self) {
        for byte in &mut self.0 {
            // SAFETY: valid unique writable byte; volatile prevents dead-store removal.
            unsafe { std::ptr::write_volatile(byte, 0) };
        }
        std::sync::atomic::compiler_fence(std::sync::atomic::Ordering::SeqCst);
    }
}
struct Record {
    grant: VerifiedGrant,
    opener: Option<Opener>,
    mount: Option<GuestMount>,
    mount_attempted: bool,
    released: bool,
}
struct Inner<P: Providers> {
    providers: P,
    root: MountRoot,
    records: HashMap<String, Record>,
}
/// Use exclusively behind one lifecycle Controller. It owns reservation limits,
/// device exclusivity, replay tombstones and service-owned RPC workers.
pub struct LinuxBackend<P: Providers> {
    inner: Arc<Mutex<Inner<P>>>,
}
impl<P: Providers> LinuxBackend<P> {
    pub fn new(providers: P) -> Result<Self, BackendError> {
        let root = MountRoot::open().map_err(|_| BackendError("invalid private mount root"))?;
        Ok(Self {
            inner: Arc::new(Mutex::new(Inner {
                providers,
                root,
                records: HashMap::new(),
            })),
        })
    }
    async fn work(
        &self,
        f: impl FnOnce(&mut Inner<P>) -> BackendResult + Send + 'static,
    ) -> BackendResult {
        let inner = self.inner.clone();
        tokio::task::spawn_blocking(move || {
            let mut guard = inner
                .lock()
                .map_err(|_| BackendError("backend poisoned; recovery required"))?;
            f(&mut guard)
        })
        .await
        .map_err(|_| BackendError("backend worker failed; recovery required"))?
    }
}
fn bound<'a>(
    records: &'a mut HashMap<String, Record>,
    g: &VerifiedGrant,
) -> Result<&'a mut Record, BackendError> {
    let r = records
        .get_mut(&g.claims().activation_id)
        .ok_or(BackendError("unknown backend activation"))?;
    if !r.grant.same_activation(g) || r.released {
        return Err(BackendError("backend activation conflict"));
    }
    Ok(r)
}
fn target(g: &VerifiedGrant, path: &str) -> BackendResult {
    if path != format!("/run/coco/storage/{}", g.claims().activation_id) {
        return Err(BackendError("unexpected guest mount path"));
    }
    Ok(())
}
#[async_trait]
impl<P: Providers> Backend for LinuxBackend<P> {
    async fn open(&self, g: &VerifiedGrant, device_id: &str) -> BackendResult {
        let g = g.clone();
        let device_id = device_id.to_owned();
        self.work(move |i| {
            let id = g.claims().activation_id.clone();
            if i.records.contains_key(&id) {
                return Err(BackendError("backend open cannot be replayed"));
            }
            i.records.insert(
                id.clone(),
                Record {
                    grant: g.clone(),
                    opener: None,
                    mount: None,
                    mount_attempted: false,
                    released: false,
                },
            );
            let resolved = i.providers.resolve(&g, &device_id)?;
            if device_id != format!("{}:{}", resolved.major, resolved.minor) {
                return Err(BackendError("resolved device mismatch"));
            }
            let device = BlockDevice::from_file(resolved.file, resolved.major, resolved.minor)
                .map_err(|_| BackendError("invalid resolved device"))?;
            let opener = Opener::prepare(g.clone(), &resolved.manifest, device)
                .map_err(|_| BackendError("header preparation failed"))?;
            let r = i.records.get_mut(&id).unwrap();
            r.opener = Some(opener); // Retain before key fetch or any kernel effects.
            let opener = r.opener.as_mut().unwrap();
            let key = i.providers.key(&g, opener.credential_uri())?;
            opener
                .open(&key.0)
                .map_err(|_| BackendError("mapping open failed"))
        })
        .await
    }
    async fn mount(&self, g: &VerifiedGrant, path: &str) -> BackendResult {
        target(g, path)?;
        let g = g.clone();
        self.work(move |i| {
            let r = bound(&mut i.records, &g)?;
            if r.mount_attempted {
                return Err(BackendError("mount cannot be replayed"));
            }
            let source = r
                .opener
                .as_ref()
                .ok_or(BackendError("mapping absent"))?
                .mapping_path()
                .map_err(|_| BackendError("mapping not open"))?;
            r.mount_attempted = true;
            r.mount = Some(
                i.root
                    .reserve(&g.claims().activation_id)
                    .map_err(|_| BackendError("mount target reservation failed"))?,
            );
            r.mount
                .as_mut()
                .unwrap()
                .mount(&source)
                .map_err(|_| BackendError("guest mount failed"))
        })
        .await
    }
    async fn unmount(&self, g: &VerifiedGrant, path: &str) -> BackendResult {
        target(g, path)?;
        let g = g.clone();
        self.work(move |i| {
            let r = bound(&mut i.records, &g)?;
            if r.mount_attempted && r.mount.is_none() {
                return Err(BackendError(
                    "uncertain target reservation; recovery required",
                ));
            }
            i.providers.detach_consumers(&g)?;
            if let Some(mount) = &mut r.mount {
                mount
                    .unmount()
                    .map_err(|_| BackendError("guest unmount failed; ownership retained"))?;
            }
            Ok(())
        })
        .await
    }
    async fn close_mapping(&self, g: &VerifiedGrant) -> BackendResult {
        let g = g.clone();
        self.work(move |i| {
            let r = bound(&mut i.records, &g)?;
            if (r.mount_attempted && r.mount.is_none())
                || r.mount.as_ref().is_some_and(|m| !m.removed())
            {
                return Err(BackendError("mount cleanup pending"));
            }
            if let Some(opener) = &mut r.opener {
                opener
                    .close()
                    .map_err(|_| BackendError("mapping cleanup failed; ownership retained"))?;
            }
            // Drop the retained raw-device fd and sealed header before authority
            // release. Closed tombstones must not pin storage or 16 MiB headers.
            r.opener = None;
            Ok(())
        })
        .await
    }
    async fn release(&self, g: &VerifiedGrant) -> BackendResult {
        let g = g.clone();
        self.work(move |i| {
            // A queued expired admission may release without ever calling open.
            let r = i
                .records
                .entry(g.claims().activation_id.clone())
                .or_insert_with(|| Record {
                    grant: g.clone(),
                    opener: None,
                    mount: None,
                    mount_attempted: false,
                    released: false,
                });
            if !r.grant.same_activation(&g) {
                return Err(BackendError("release binding mismatch"));
            }
            if r.released {
                return Ok(());
            }
            if (r.mount_attempted && r.mount.is_none())
                || r.mount.as_ref().is_some_and(|m| !m.removed())
                || r.opener
                    .as_ref()
                    .is_some_and(|o| o.phase() != Phase::Closed)
            {
                return Err(BackendError("storage effects remain; release denied"));
            }
            i.providers.release(&g)?;
            r.released = true;
            Ok(())
        })
        .await
    }
}
