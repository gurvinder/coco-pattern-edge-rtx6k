// SPDX-License-Identifier: Apache-2.0
//! Guest-service primitives, including Linux reopen/close and staging mounts. No
//! host-facing RPC, attestation verifier or production namespace adapter is supplied.
//! Applications must never receive access to this service.
#[cfg(target_os = "linux")]
pub mod guest_mount;
pub mod lifecycle;
#[cfg(target_os = "linux")]
pub mod linux_backend;
#[cfg(target_os = "linux")]
pub mod opener;
pub mod profile;
pub mod receipt;
#[cfg(target_os = "linux")]
pub mod sealed_header;
