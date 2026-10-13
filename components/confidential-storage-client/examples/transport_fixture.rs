// SPDX-License-Identifier: Apache-2.0
//! Synthetic evidence for cross-language TLS tests ONLY; not a guest executable.
use confidential_storage_client::{AuthorityClient, Config, Evidence, Result};
use confidential_storage_guest::receipt::Expected;
use serde_json::{json, Value};
use std::{fs, net::SocketAddr};
struct FixtureEvidence;
impl Evidence for FixtureEvidence {
    fn collect(&self, digest: [u8; 32]) -> Result<Vec<u8>> {
        Ok(serde_json::to_vec(&json!({"SYNTHETIC_TEST_RUNTIME_DIGEST":digest.iter().map(|b|format!("{b:02x}")).collect::<String>()})).unwrap())
    }
}
fn main() {
    let input = std::env::args()
        .nth(1)
        .expect("explicit temporary fixture configuration");
    let v: Value = serde_json::from_slice(&fs::read(input).unwrap()).unwrap();
    let c = Config {
        audience: v["audience"].as_str().unwrap().into(),
        certificate_der: fs::read(v["certificate_der"].as_str().unwrap()).unwrap(),
        address: Some(
            v["address"]
                .as_str()
                .unwrap()
                .parse::<SocketAddr>()
                .unwrap(),
        ),
        policy_sha256: v["policy_sha256"].as_str().unwrap().into(),
        init_data: v["init_data"].as_str().unwrap().into(),
        receipt_public_key: serde_json::from_value(v["receipt_public_key"].clone()).unwrap(),
        receipt_issuer: v["receipt_issuer"].as_str().unwrap().into(),
        receipt_key_id: v["receipt_key_id"].as_str().unwrap().into(),
    };
    let client = AuthorityClient::new(c, FixtureEvidence).unwrap();
    let mut expected: Expected = serde_json::from_value(v["expected"].clone()).unwrap();
    expected.session_sha256 = client.session_sha256().into();
    match client.reopen(&expected) {
        Err(_) if v["deny"] == true => {
            println!("PASS denied fixture request");
        }
        Ok(grant) if v["deny"] != true => {
            let retry = client.reopen(&expected).unwrap();
            assert_eq!(grant.claims().activation_id, retry.claims().activation_id);
            client.close(&grant).unwrap();
            client.close(&grant).unwrap();
            assert!(client.reopen(&expected).is_err());
            println!(
                "PASS TLS receipt verification, stable-session retry, close, durable replay denial"
            );
        }
        _ => panic!("unexpected fixture authorization result"),
    }
}
