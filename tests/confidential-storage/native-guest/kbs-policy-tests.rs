// Run as a policy-engine example against the exact pinned Trustee workspace.
// Synthetic policy fixtures only: these do not constitute hardware attestation.
use serde_json::{json, Value};
fn allowed(policy: &str, data: &Value, input: &Value) -> bool {
    let mut engine = regorus::Engine::new();
    engine
        .add_policy("release.rego".into(), policy.into())
        .unwrap();
    engine
        .add_data(regorus::Value::from_json_str(&data.to_string()).unwrap())
        .unwrap();
    engine.set_input_json(&input.to_string()).unwrap();
    serde_json::to_value(engine.eval_rule("data.policy.allow".into()).unwrap()).unwrap()
        == json!(true)
}
fn main() {
    let path = std::env::args().nth(1).unwrap();
    let policy = std::fs::read_to_string(path).unwrap();
    let line = policy
        .lines()
        .find(|s| s.starts_with("expected := "))
        .unwrap();
    let e: Value = serde_json::from_str(line.strip_prefix("expected := ").unwrap()).unwrap();
    let resource = std::env::args()
        .nth(2)
        .unwrap_or("storage-test/model-keys/qwen-luks-v1".into());
    let data = json!({"plugin":"resource","resource-path":resource.split('/').collect::<Vec<_>>(),"query":{}});
    let input = json!({"iss":"storage-kbs-as","submods":{"cpu0":{
        "ear.status":"affirming","ear.appraisal-policy-id":"default",
        "ear.trustworthiness-vector":{"executables":3,"hardware":2,"configuration":2},
        "ear.veraison.annotated-evidence":{"init_data":e["init_data"],"snp":{
            "measurement":e["measurement"],"reported_tcb_bootloader":e["tcb"]["bootloader"],
            "reported_tcb_tee":e["tcb"]["tee"],"reported_tcb_snp":e["tcb"]["snp"],
            "reported_tcb_microcode":e["tcb"]["microcode"],"policy_abi_major":0,"policy_abi_minor":0,
            "policy_smt_allowed":true,"policy_migrate_ma":false,"policy_debug_allowed":false,"policy_single_socket":false}}
    }}});
    assert!(allowed(&policy, &data, &input));
    let mut negatives = 0;
    for (path, value) in [
        ("/iss", json!("untrusted")),
        ("/submods/cpu0/ear.status", json!("contraindicated")),
        ("/submods/cpu0/ear.appraisal-policy-id", json!("other")),
        (
            "/submods/cpu0/ear.trustworthiness-vector/executables",
            json!(33),
        ),
        (
            "/submods/cpu0/ear.trustworthiness-vector/hardware",
            json!(97),
        ),
        (
            "/submods/cpu0/ear.trustworthiness-vector/configuration",
            json!(36),
        ),
        (
            "/submods/cpu0/ear.veraison.annotated-evidence/init_data",
            json!("0".repeat(64)),
        ),
        (
            "/submods/cpu0/ear.veraison.annotated-evidence/snp/measurement",
            json!("0".repeat(96)),
        ),
        (
            "/submods/cpu0/ear.veraison.annotated-evidence/snp/reported_tcb_microcode",
            json!(0),
        ),
        (
            "/submods/cpu0/ear.veraison.annotated-evidence/snp/policy_debug_allowed",
            json!(true),
        ),
        (
            "/submods/cpu0/ear.veraison.annotated-evidence/snp/policy_migrate_ma",
            json!(true),
        ),
        ("/submods", json!({})),
    ] {
        let mut bad = input.clone();
        *bad.pointer_mut(path).unwrap() = value;
        assert!(!allowed(&policy, &data, &bad), "accepted {path}");
        negatives += 1;
    }
    for (path, value) in [
        ("/plugin", json!("other")),
        (
            "/resource-path",
            json!(["default", "model-keys", "edge-model-key"]),
        ),
        ("/query", json!({"bypass":"1"})),
    ] {
        let mut bad = data.clone();
        *bad.pointer_mut(path).unwrap() = value;
        assert!(!allowed(&policy, &bad, &input), "accepted {path}");
        negatives += 1;
    }
    assert!(!allowed(
        "package policy\nimport rego.v1\ndefault allow := false\n",
        &data,
        &input
    ));
    println!(
        "KBS_POLICY_TEST_PASS positive=1 negatives={} deny_all=1",
        negatives
    );
}
