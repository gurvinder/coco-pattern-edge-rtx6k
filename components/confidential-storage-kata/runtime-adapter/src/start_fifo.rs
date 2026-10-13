// SPDX-License-Identifier: Apache-2.0
//! Bounded handshake around pinned rustjail's single-byte FIFO start write.
//! Directory/FIFO mutation and additional writers are excluded by trusted guest
//! service ownership. This is not an API for application/host-controlled paths.
use std::{
    fs::{self, File, OpenOptions},
    io,
    os::{
        fd::AsRawFd,
        unix::fs::{FileTypeExt, MetadataExt, OpenOptionsExt},
    },
    path::{Path, PathBuf},
};
pub(crate) struct StartFifo {
    pinned: File,
    directory: File,
    path: PathBuf,
}
/// Both ends stay open until rustjail's single-byte write completes. Neither is
/// used to read/write data. Dropping them cannot strand a live child at open():
/// the writer is acquired FIRST, only when the real child has a pending reader.
pub(crate) struct ArmedFifo {
    _writer: File,
    _reader: File,
}
fn invalid() -> io::Error {
    io::Error::other("runtime start FIFO identity or ownership changed")
}
impl StartFifo {
    pub(crate) fn capture(root: &Path) -> io::Result<Self> {
        if !root.is_absolute()
            || root.components().any(|c| {
                !matches!(
                    c,
                    std::path::Component::RootDir | std::path::Component::Normal(_)
                )
            })
        {
            return Err(invalid());
        }
        for ancestor in root.ancestors() {
            let m = fs::symlink_metadata(ancestor)?;
            if !m.is_dir() || m.uid() != 0 || m.mode() & 0o022 != 0 {
                return Err(invalid());
            }
        }
        let directory = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(root)?;
        let path = root.join(rustjail::container::EXEC_FIFO_FILENAME);
        let pinned = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_PATH | libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(&path)?;
        let this = Self {
            pinned,
            directory,
            path,
        };
        this.check()?;
        Ok(this)
    }
    fn check(&self) -> io::Result<()> {
        let root = fs::symlink_metadata(self.path.parent().ok_or_else(invalid)?)?;
        let dir = self.directory.metadata()?;
        let current = fs::symlink_metadata(&self.path)?;
        let pinned = self.pinned.metadata()?;
        if !root.is_dir()
            || root.uid() != 0
            || root.mode() & 0o022 != 0
            || !current.file_type().is_fifo()
            || current.uid() != 0
            || current.mode() & 0o022 != 0
            || (current.dev(), current.ino()) != (pinned.dev(), pinned.ino())
            || (root.dev(), root.ino()) != (dir.dev(), dir.ino())
        {
            return Err(invalid());
        }
        Ok(())
    }
    pub(crate) fn arm(&self) -> io::Result<Option<ArmedFifo>> {
        self.check()?;
        let pinned_path = format!("/proc/self/fd/{}", self.pinned.as_raw_fd());
        let writer = match OpenOptions::new()
            .write(true)
            .custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC)
            .open(&pinned_path)
        {
            Ok(file) => file,
            Err(e) if e.raw_os_error() == Some(libc::ENXIO) => return Ok(None),
            Err(e) => return Err(e),
        };
        // Do not create this reader before the writer handshake: that would
        // allow start to return before the child even opened its FIFO.
        let reader = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC)
            .open(&pinned_path)?;
        let mut pending = 0i32;
        if unsafe { libc::ioctl(reader.as_raw_fd(), libc::FIONREAD, &mut pending) } != 0 {
            return Err(io::Error::last_os_error());
        }
        if pending != 0 {
            return Err(invalid());
        }
        self.check()?;
        // With no other writers, an empty pipe and exactly one byte written,
        // neither rustjail's open nor its write can block or get SIGPIPE.
        Ok(Some(ArmedFifo {
            _writer: writer,
            _reader: reader,
        }))
    }
}
