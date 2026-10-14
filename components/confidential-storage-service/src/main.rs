// SPDX-License-Identifier: Apache-2.0
//! Private guest service. No HTTP listener, application API or key export.
use confidential_storage_client::{private_aa::PrivateAa, AuthorityClient};
use confidential_storage_guest::{
    guest_mount::{GuestMount, MountRoot},
    opener::BlockDevice,
    receipt::{Expected, VerifiedGrant},
    ro_opener::RoOpener,
};
use confidential_storage_kbs::SnpCpuKbs;
use confidential_storage_service::{
    invalid, read_frame, write_frame, Request, Reservation, Response, CONFIG, SOCKET,
};
use ring::rand::{SecureRandom, SystemRandom};
use serde::Deserialize;
use std::{
    fs::{self, File, OpenOptions},
    io::{self, Read},
    net::SocketAddr,
    os::{
        fd::AsRawFd,
        unix::{
            fs::{MetadataExt, OpenOptionsExt, PermissionsExt},
            net::{UnixListener, UnixStream},
        },
    },
    path::Path,
    time::Duration,
};
use zeroize::Zeroizing;
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
const INITDATA: &str = "/run/confidential-containers/initdata/initdata.toml";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Authority {
    audience: String,
    certificate_der: Vec<u8>,
    address: Option<SocketAddr>,
    policy_sha256: String,
    init_data: String,
    receipt_public_key: [u8; 32],
    receipt_issuer: String,
    receipt_key_id: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Kbs {
    origin: String,
    certificate_der: Vec<u8>,
    address: Option<SocketAddr>,
    allowed_resources: Vec<String>,
    require_gpu: bool,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    schema_version: u32,
    purpose: String,
    authority: Authority,
    kbs: Kbs,
    expected: Expected,
    manifest: Vec<u8>,
}

fn fixed_file(path: &str, maximum: usize, exact_mode: Option<u32>) -> Result<Vec<u8>> {
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)?;
    let info = file.metadata()?;
    if !info.is_file()
        || info.uid() != 0
        || info.mode() & 0o022 != 0
        || exact_mode.is_some_and(|m| info.mode() & 0o777 != m)
        || info.len() > maximum as u64
    {
        return Err(invalid().into());
    }
    let mut data = Vec::new();
    (&mut file)
        .take(maximum as u64 + 1)
        .read_to_end(&mut data)?;
    if data.len() > maximum {
        return Err(invalid().into());
    }
    Ok(data)
}
fn random_id() -> Result<String> {
    let mut bytes = [0u8; 32];
    SystemRandom::new()
        .fill(&mut bytes)
        .map_err(|_| invalid())?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}
fn bootstrap() -> Result<(Config, String)> {
    // No dumpable service or swap-backed secret memory. No privilege can be
    // gained through helper executables; all storage helpers run as guest root.
    if unsafe { libc::geteuid() } != 0
        || unsafe { libc::prctl(libc::PR_SET_DUMPABLE, 0) } != 0
        || unsafe { libc::prctl(libc::PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) } != 0
    {
        return Err(invalid().into());
    }
    let core = libc::rlimit {
        rlim_cur: 0,
        rlim_max: 0,
    };
    if unsafe { libc::setrlimit(libc::RLIMIT_CORE, &core) } != 0 {
        return Err(invalid().into());
    }
    let mem = fs::read_to_string("/proc/meminfo")?;
    if !mem
        .lines()
        .any(|line| line.split_whitespace().collect::<Vec<_>>() == ["SwapTotal:", "0", "kB"])
    {
        return Err(invalid().into());
    }
    let config: Config = serde_json::from_slice(&fixed_file(CONFIG, 256 * 1024, Some(0o600))?)?;
    // Initial integration accepts only the immutable published-model path.
    // RW initialization must be wired to the signed journal, never auto-format.
    if config.schema_version != 1
        || config.purpose != "isolated-confidential-storage/v1"
        || config.expected.access != "ro"
        || config.expected.destination != "/models"
        || config.expected.profile_sha256
            != confidential_storage_guest::ro_profile::profile_sha256()
        || config.manifest.is_empty()
        || config.manifest.len() > 64 * 1024
    {
        return Err(invalid().into());
    }
    let initdata = String::from_utf8(fixed_file(INITDATA, 64 * 1024, None)?)?;
    Ok((config, initdata))
}

struct Service {
    reservation: Reservation,
    stage: &'static str,
    authority: AuthorityClient<PrivateAa>,
    kbs: SnpCpuKbs,
    runtime: tokio::runtime::Runtime,
    expected: Expected,
    manifest: Vec<u8>,
    root: MountRoot,
    grant: Option<VerifiedGrant>,
    opener: Option<RoOpener>,
    mount: Option<GuestMount>,
    authorization_attempted: bool,
    effects_attempted: bool,
    closing: bool,
    mounted: bool,
}
impl Service {
    fn new(config: Config, initdata: String) -> Result<Self> {
        let a = config.authority;
        let authority = AuthorityClient::new(
            confidential_storage_client::Config {
                audience: a.audience,
                certificate_der: a.certificate_der,
                address: a.address,
                policy_sha256: a.policy_sha256,
                init_data: a.init_data,
                receipt_public_key: a.receipt_public_key,
                receipt_issuer: a.receipt_issuer,
                receipt_key_id: a.receipt_key_id,
            },
            PrivateAa,
        )?;
        let k = config.kbs;
        let kbs = SnpCpuKbs::new(
            &authority,
            confidential_storage_kbs::Config {
                origin: k.origin,
                certificate_der: k.certificate_der,
                address: k.address,
                init_data_toml: initdata,
                allowed_resources: k.allowed_resources,
                require_gpu: k.require_gpu,
            },
        )?;
        let mut expected = config.expected;
        expected.session_sha256 = authority.session_sha256().into();
        expected.operation_id = random_id()?;
        expected.nonce = random_id()?;
        Ok(Self {
            reservation: Reservation::default(),
            stage: "ready",
            authority,
            kbs,
            runtime: tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()?,
            expected,
            manifest: config.manifest,
            root: MountRoot::open()?,
            grant: None,
            opener: None,
            mount: None,
            authorization_attempted: false,
            effects_attempted: false,
            closing: false,
            mounted: false,
        })
    }
    fn response(&self) -> Result<Response> {
        let grant = self.grant.as_ref().ok_or_else(invalid)?;
        if !self.mounted || self.closing {
            return Err(invalid().into());
        }
        Ok(Response::Mounted {
            source: format!("/run/coco/storage/{}", grant.claims().activation_id),
            destination: self.expected.destination.clone(),
            readonly: true,
        })
    }
    fn activate(&mut self, request: &Request) -> Result<Response> {
        self.stage = "reserve";
        self.reservation.reserve(request)?;
        if self.closing {
            return Err(invalid().into());
        }
        if self.mounted {
            return self.response();
        }
        if self.effects_attempted {
            return Err(invalid().into());
        }
        let Request::Activate {
            device_path,
            major,
            minor,
            ..
        } = request
        else {
            return Err(invalid().into());
        };
        self.stage = "resolve-device";
        let file = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(device_path)?;
        let device = BlockDevice::from_file(file, *major, *minor)?;
        self.stage = "authority-reopen";
        self.authorization_attempted = true;
        let (grant, permit) = self.authority.authorize_reopen_key(&self.expected)?;
        self.grant = Some(grant.clone());
        self.effects_attempted = true; // Subsequent failures require explicit close, never re-open.
                                       // Authenticate copied header/profile/manifest BEFORE requesting the key.
        self.stage = "authenticate-header";
        self.opener = Some(RoOpener::prepare(grant.clone(), &self.manifest, device)?);
        self.stage = "kbs-retrieve";
        let material = self.runtime.block_on(self.kbs.retrieve(permit))?;
        let key = Zeroizing::new(<[u8; 32]>::try_from(material.as_bytes()).map_err(|_| invalid())?);
        self.stage = "open-mappings";
        self.opener.as_mut().unwrap().open(&key)?;
        drop(key);
        drop(material);
        self.mount = Some(self.root.reserve(&grant.claims().activation_id)?);
        let mapping = self.opener.as_ref().unwrap().mapping_path()?;
        self.mount.as_mut().unwrap().mount_readonly(&mapping)?;
        self.mounted = true;
        eprintln!("STORAGE_SERVICE mounted_ro");
        self.response()
    }
    fn close(&mut self, request: &Request) -> Result<Response> {
        self.stage = "close";
        self.reservation.check_close(request)?;
        self.closing = true; // Admission stays closed even if cleanup fails.
        if self.reservation.closed() {
            return Ok(Response::Closed);
        }
        // Agent calls only after destroying the consumer. Kernel busy checks
        // independently prevent key-owner release if an app bind still exists.
        if let Some(mount) = &mut self.mount {
            mount.unmount()?;
        }
        if let Some(opener) = &mut self.opener {
            opener.close()?;
        }
        if self.grant.is_none() && self.authorization_attempted {
            // A response may have been lost after the authority committed owner.
            // Recover only the SAME retained session/operation, then close it.
            self.grant = Some(self.authority.reopen(&self.expected)?);
        }
        if let Some(grant) = &self.grant {
            self.authority.close(grant)?;
        }
        self.reservation.mark_closed();
        eprintln!("STORAGE_SERVICE closed");
        Ok(Response::Closed)
    }
    fn handle(&mut self, request: &Request) -> Result<Response> {
        match request {
            Request::Activate { .. } => self.activate(request),
            Request::Close { .. } => self.close(request),
        }
    }
}

fn check_peer(stream: &UnixStream, agent: &File) -> Result<()> {
    let mut cred: libc::ucred = unsafe { std::mem::zeroed() };
    let mut n = std::mem::size_of::<libc::ucred>() as libc::socklen_t;
    if unsafe {
        libc::getsockopt(
            stream.as_raw_fd(),
            libc::SOL_SOCKET,
            libc::SO_PEERCRED,
            (&mut cred as *mut libc::ucred).cast(),
            &mut n,
        )
    } != 0
        || n as usize != std::mem::size_of::<libc::ucred>()
        || cred.uid != 0
        || cred.pid <= 1
    {
        return Err(invalid().into());
    }
    // Additional restriction within the trusted VM-root boundary: no executable
    // pathname supplied by a caller is accepted. Applications are non-root and
    // cannot reach this private socket; the measured pause process is trusted.
    let exe = File::open(format!("/proc/{}/exe", cred.pid))?.metadata()?;
    let pinned = agent.metadata()?;
    if exe.dev() != pinned.dev() || exe.ino() != pinned.ino() {
        return Err(invalid().into());
    }
    Ok(())
}
fn main() -> Result<()> {
    let (config, initdata) = bootstrap()?;
    let mut service = Service::new(config, initdata)?;
    let directory = Path::new(SOCKET).parent().unwrap();
    let info = fs::symlink_metadata(directory)?;
    if !info.is_dir() || info.uid() != 0 || info.mode() & 0o777 != 0o700 {
        return Err(invalid().into());
    }
    // Never unlink/adopt an existing socket or restart over retained VM state.
    let listener = UnixListener::bind(SOCKET)?;
    fs::set_permissions(SOCKET, fs::Permissions::from_mode(0o600))?;
    let agent = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open("/usr/bin/kata-agent")?;
    let metadata = agent.metadata()?;
    if !metadata.is_file() || metadata.uid() != 0 || metadata.mode() & 0o022 != 0 {
        return Err(invalid().into());
    }
    eprintln!("STORAGE_SERVICE ready");
    for stream in listener.incoming() {
        let mut stream = stream?;
        stream.set_read_timeout(Some(Duration::from_secs(5)))?;
        stream.set_write_timeout(Some(Duration::from_secs(5)))?;
        if check_peer(&stream, &agent).is_err() {
            continue;
        }
        let response = (|| -> Result<Response> {
            let request: Request = serde_json::from_slice(&read_frame(&mut stream)?)?;
            request.validate()?;
            service.handle(&request)
        })()
        .unwrap_or_else(|error| {
            // Classify only our fixed error variants, never format external errors.
            let category = match error.downcast_ref::<confidential_storage_client::Error>() {
                Some(confidential_storage_client::Error::Configuration) => "authority-config",
                Some(confidential_storage_client::Error::Transport) => "authority-transport",
                Some(confidential_storage_client::Error::Binding) => "authority-binding",
                Some(confidential_storage_client::Error::Attestation) => "private-attestation",
                Some(confidential_storage_client::Error::Receipt(_)) => "authority-receipt",
                None => "local-or-kbs",
            };
            // Log only fixed labels: never errors, requests, evidence or keys.
            eprintln!(
                "STORAGE_SERVICE denied_state_retained stage={} category={}",
                service.stage, category
            );
            Response::Denied
        });
        // Disconnect does not cancel an effect or discard the retained service.
        let _ = write_frame(&mut stream, &serde_json::to_vec(&response)?);
    }
    Err(io::Error::other("private listener ended").into())
}
