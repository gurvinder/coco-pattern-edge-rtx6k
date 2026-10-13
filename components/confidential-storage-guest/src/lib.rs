// SPDX-License-Identifier: Apache-2.0
//! Guest-service primitives, including a Linux reopen/close operation. No
//! host-facing RPC, attestation verifier or complete mount backend is supplied.
//! Applications must never receive access to this service.
pub mod lifecycle;
#[cfg(target_os = "linux")]
pub mod opener;
pub mod profile;
pub mod receipt;
#[cfg(target_os = "linux")]
pub mod sealed_header;
