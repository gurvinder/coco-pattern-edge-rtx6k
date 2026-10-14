// SPDX-License-Identifier: Apache-2.0
//! Private guest service. No HTTP listener, application API or key export.
use confidential_storage_client::{initialization as init, private_aa::PrivateAa, AuthorityClient};
use confidential_storage_guest::{
    guest_mount::{GuestMount, MountRoot},
    initializer::Initializer,
    opener::{BlockDevice, Opener},
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
    #[serde(default)]
    initialization_plan_sha256: Option<String>,
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
fn sha(bytes: &[u8]) -> String {
    ring::digest::digest(&ring::digest::SHA256, bytes)
        .as_ref()
        .iter()
        .map(|v| format!("{v:02x}"))
        .collect()
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
    let profile_ok = match config.expected.access.as_str() {
        "ro" => {
            config.expected.destination == "/models"
                && config.expected.profile_sha256
                    == confidential_storage_guest::ro_profile::profile_sha256()
                && !config.manifest.is_empty()
                && config.initialization_plan_sha256.is_none()
        }
        "rw" => {
            config.expected.destination == "/data"
                && config.expected.profile_sha256
                    == confidential_storage_guest::profile::profile_sha256()
                && config.manifest.is_empty()
                && config.initialization_plan_sha256.as_ref().is_some_and(|s| {
                    s.len() == 64
                        && s.bytes()
                            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                })
        }
        _ => false,
    };
    if config.schema_version != 1
        || config.purpose != "isolated-confidential-storage/v1"
        || !profile_ok
        || config.manifest.len() > 64 * 1024
    {
        return Err(invalid().into());
    }
    let initdata = String::from_utf8(fixed_file(INITDATA, 64 * 1024, None)?)?;
    Ok((config, initdata))
}

enum StorageOpener {
    Ro(RoOpener),
    Rw(Opener),
}
impl StorageOpener {
    fn open(&mut self, key: &[u8; 32]) -> Result<()> {
        match self {
            Self::Ro(x) => x.open(key)?,
            Self::Rw(x) => x.open(key)?,
        };
        Ok(())
    }
    fn close(&mut self) -> Result<()> {
        match self {
            Self::Ro(x) => x.close()?,
            Self::Rw(x) => x.close()?,
        };
        Ok(())
    }
    fn mapping_path(&self) -> Result<std::path::PathBuf> {
        Ok(match self {
            Self::Ro(x) => x.mapping_path()?,
            Self::Rw(x) => x.mapping_path()?,
        })
    }
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
    opener: Option<StorageOpener>,
    initializer: Option<Initializer>,
    initialization_plan_sha256: Option<String>,
    initialization_attempted: bool,
    initialization_ready: Option<init::Record>,
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
            initializer: None,
            initialization_plan_sha256: config.initialization_plan_sha256,
            initialization_attempted: false,
            initialization_ready: None,
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
            readonly: self.expected.access == "ro",
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
            .write(self.expected.access == "rw")
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(device_path)?;
        if self.expected.access == "rw" {
            self.effects_attempted = true;
            self.prepare_rw(&file, *major, *minor)?;
        }
        let device = BlockDevice::from_file(file, *major, *minor)?;
        self.stage = "authority-reopen";
        self.authorization_attempted = true;
        let (grant, permit) = self.authority.authorize_reopen_key(&self.expected)?;
        self.grant = Some(grant.clone());
        self.effects_attempted = true; // Subsequent failures require explicit close, never re-open.
                                       // Authenticate copied header/profile/manifest BEFORE requesting the key.
        self.stage = "authenticate-header";
        self.opener = Some(if self.expected.access == "ro" {
            StorageOpener::Ro(RoOpener::prepare(grant.clone(), &self.manifest, device)?)
        } else {
            StorageOpener::Rw(Opener::prepare(grant.clone(), &self.manifest, device)?)
        });
        self.stage = "kbs-retrieve";
        let material = self.runtime.block_on(self.kbs.retrieve(permit))?;
        let key = Zeroizing::new(<[u8; 32]>::try_from(material.as_bytes()).map_err(|_| invalid())?);
        self.stage = "open-mappings";
        self.opener.as_mut().unwrap().open(&key)?;
        drop(key);
        drop(material);
        self.mount = Some(self.root.reserve(&grant.claims().activation_id)?);
        let mapping = self.opener.as_ref().unwrap().mapping_path()?;
        if self.expected.access == "ro" {
            self.mount.as_mut().unwrap().mount_readonly(&mapping)?;
        } else {
            self.mount.as_mut().unwrap().mount(&mapping)?;
        }
        self.mounted = true;
        eprintln!(
            "STORAGE_SERVICE {}",
            if self.expected.access == "ro" {
                "mounted_ro"
            } else {
                "mounted_rw"
            }
        );
        self.response()
    }
    fn prepare_rw(&mut self, file: &File, major: u32, minor: u32) -> Result<()> {
        self.stage = "initialization-plan";
        let request = init::Request {
            expected: self.expected.clone(),
            plan_sha256: self
                .initialization_plan_sha256
                .clone()
                .ok_or_else(invalid)?,
        };
        let init::Decision::Plan(plan) = self
            .authority
            .initialization(&request, init::Operation::Plan)?
        else {
            return Err(invalid().into());
        };
        match plan.state() {
            init::State::Ready => {
                self.manifest = plan.ready_manifest()?;
                return Ok(());
            }
            init::State::Initializing => return Err(invalid().into()),
            init::State::Uninitialized => (),
        }
        let device = BlockDevice::from_file(file.try_clone()?, major, minor)?;
        Initializer::check_blank(&device, plan.plan().device_bytes)?;
        self.stage = "initialization-begin";
        self.initialization_attempted = true;
        let init::Decision::Begin {
            fresh: true,
            record,
        } = self
            .authority
            .initialization(&request, init::Operation::Begin)?
        else {
            return Err(invalid().into());
        };
        let activation = record.activation_id().ok_or_else(invalid)?.to_owned();
        self.initializer = Some(Initializer::preflight(
            device,
            record.plan().device_bytes,
            &record.plan().luks_uuid,
            &activation,
        )?);
        self.stage = "initialization-key";
        let permit = self.authority.authorize_initialization_key(&request)?;
        let material = self.runtime.block_on(self.kbs.retrieve(permit))?;
        let key = Zeroizing::new(<[u8; 32]>::try_from(material.as_bytes()).map_err(|_| invalid())?);
        for step in [
            init::Step::LuksFormat,
            init::Step::IntegrityInitialize,
            init::Step::FilesystemCreate,
            init::Step::DurabilityBarrier,
        ] {
            self.stage = "initialization-step";
            let init::Decision::Step(init::StepDecision::Execute(permit)) =
                self.authority.initialization(
                    &request,
                    init::Operation::Step {
                        activation_id: activation.clone(),
                        step,
                    },
                )?
            else {
                return Err(invalid().into());
            };
            let execution = permit.consume()?;
            let backend = self.initializer.as_mut().ok_or_else(invalid)?;
            let mut result = init::Completion {
                header_sha256: String::new(),
                stack_sha256: None,
                filesystem_uuid: None,
                manifest_sha256: None,
            };
            match step {
                init::Step::LuksFormat => {
                    self.stage = "luks-format";
                    backend.format(&key)?;
                }
                init::Step::IntegrityInitialize => {
                    self.stage = "integrity-initialize";
                    result.stack_sha256 = Some(backend.initialize_integrity(&key)?);
                }
                init::Step::FilesystemCreate => {
                    self.stage = "filesystem-create";
                    result.filesystem_uuid = Some(backend.create_filesystem()?);
                }
                init::Step::DurabilityBarrier => {
                    self.stage = "durability-barrier";
                    backend.barrier()?;
                    let manifest = record.manifest(backend.header_sha256()?)?;
                    result.manifest_sha256 = Some(sha(&manifest));
                }
            }
            result.header_sha256 = backend.header_sha256()?.to_owned();
            let evidence = serde_json::to_vec(
                &serde_json::json!({"activation":activation,"step":step,"step_id":execution.step_id(),"result":result}),
            )?;
            let init::Decision::Completed = self.authority.initialization(
                &request,
                init::Operation::Complete {
                    activation_id: activation.clone(),
                    step,
                    step_id: execution.step_id().into(),
                    result,
                    evidence_sha256: sha(&evidence),
                },
            )?
            else {
                return Err(invalid().into());
            };
            eprintln!("STORAGE_SERVICE initialization_step_complete {:?}", step);
        }
        drop(key);
        drop(material);
        let header = self
            .initializer
            .as_ref()
            .ok_or_else(invalid)?
            .header_sha256()?
            .to_owned();
        let init::Decision::Ready(ready) = self.authority.initialization(
            &request,
            init::Operation::Ready {
                activation_id: activation,
                header_sha256: header,
            },
        )?
        else {
            return Err(invalid().into());
        };
        self.manifest = ready.ready_manifest()?;
        self.initialization_ready = Some(ready);
        self.authority
            .close_initialization(self.initialization_ready.as_ref().unwrap())?;
        self.initialization_attempted = false;
        self.initializer = None;
        // Ready initialization is not a reopen grant. Acquire a separate fresh
        // operation and permit through the ordinary authenticated-header opener.
        self.expected.operation_id = random_id()?;
        self.expected.nonce = random_id()?;
        eprintln!("STORAGE_SERVICE initialized_ready");
        Ok(())
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
        if let Some(initializer) = &mut self.initializer {
            initializer.close_mappings()?;
        }
        if self.initialization_attempted {
            // Never release/reset uncertain initialization ownership. A verified
            // Ready result plus proven mapping close permits idempotent release.
            let ready = self.initialization_ready.as_ref().ok_or_else(invalid)?;
            self.authority.close_initialization(ready)?;
            self.initialization_attempted = false;
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
