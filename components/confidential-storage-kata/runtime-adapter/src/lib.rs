// SPDX-License-Identifier: Apache-2.0
//! Development adapter for a pinned experimental Kata source. No host RPC or
//! attested provider is installed. The transport patch's hard gate stays closed.
pub mod process;
mod process_guard;
pub mod runtime;
mod start_fifo;
