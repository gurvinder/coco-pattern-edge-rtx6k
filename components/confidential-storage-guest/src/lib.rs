// SPDX-License-Identifier: Apache-2.0
//! Guest-service primitives. No host-facing RPC, attestation verifier or disk
//! backend is supplied. Applications must never receive access to this service.
pub mod lifecycle;
pub mod profile;
pub mod receipt;
#[cfg(target_os = "linux")]
pub mod sealed_header;
