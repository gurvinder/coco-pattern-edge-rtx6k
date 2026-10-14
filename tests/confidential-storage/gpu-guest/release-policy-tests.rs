// Synthetic claims exercise the actual rendered Rego, not hardware signatures.
// Run in the pinned kata-agent-policy crate as an example (Regorus dependency).
use serde_json::{json, Value};
fn allowed(policy: &str, tag: &str, input: &Value) -> bool {
    let mut engine = regorus::Engine::new();
    engine
        .add_policy("release.rego".into(), policy.into())
        .unwrap();
    engine
        .add_data(
            regorus::Value::from_json_str(
                &json!({
                    "plugin":"resource", "resource-path":["default","model-keys",tag]
                })
                .to_string(),
            )
            .unwrap(),
        )
        .unwrap();
    engine.set_input_json(&input.to_string()).unwrap();
    serde_json::to_value(engine.eval_rule("data.policy.allow".into()).unwrap()).unwrap()
        == json!(true)
}
fn main() {
    let policy = std::fs::read_to_string(std::env::args().nth(1).unwrap()).unwrap();
    let bindings: Value = serde_json::from_str(
        policy
            .lines()
            .find_map(|line| line.strip_prefix("model_key_bindings := "))
            .unwrap(),
    )
    .unwrap();
    let expected = &bindings["qwen-gpu-model-v1"];
    let input = json!({"submods":{
        "cpu0":{"ear.status":"affirming", "ear.trustworthiness-vector":{
            "hardware":2,"configuration":3,"executables":3},
            "ear.veraison.annotated-evidence":{"init_data":expected["initData"],"snp":{
                "measurement":expected["launchMeasurement"],
                "policy_debug_allowed":false,"policy_migrate_ma":false}}},
        "gpu0":{"ear.status":"affirming", "ear.trustworthiness-vector":{
            "hardware":2,"configuration":3,"executables":3},
            "ear.veraison.annotated-evidence":{"nvidia":{
                "x-nvidia-overall-att-result":true,"secboot":true,
                "dbgstat":"disabled","measres":"success"}}}
    }});
    let mut cases = 0;
    for tag in ["qwen-gpu-model-v1", "qwen-gpu-data-v1"] {
        assert!(allowed(&policy, tag, &input));
        cases += 1;
        for (path, value) in [
            (
                "/submods/cpu0/ear.veraison.annotated-evidence/init_data",
                json!("wrong"),
            ),
            (
                "/submods/cpu0/ear.veraison.annotated-evidence/snp/measurement",
                json!("wrong"),
            ),
            (
                "/submods/cpu0/ear.veraison.annotated-evidence/snp/policy_debug_allowed",
                json!(true),
            ),
            (
                "/submods/cpu0/ear.veraison.annotated-evidence/snp/policy_migrate_ma",
                json!(true),
            ),
            (
                "/submods/gpu0/ear.veraison.annotated-evidence/nvidia/secboot",
                json!(false),
            ),
            (
                "/submods/gpu0/ear.veraison.annotated-evidence/nvidia/dbgstat",
                json!("enabled"),
            ),
            (
                "/submods/gpu0/ear.veraison.annotated-evidence/nvidia/measres",
                json!("failure"),
            ),
            (
                "/submods/gpu0/ear.veraison.annotated-evidence/nvidia/x-nvidia-overall-att-result",
                json!(false),
            ),
        ] {
            let mut bad = input.clone();
            *bad.pointer_mut(path).unwrap() = value;
            assert!(!allowed(&policy, tag, &bad), "{} {}", tag, path);
            cases += 1;
        }
        for device in ["cpu0", "gpu0"] {
            let mut missing = input.clone();
            missing["submods"].as_object_mut().unwrap().remove(device);
            assert!(!allowed(&policy, tag, &missing));
            cases += 1;
            for field in ["hardware", "configuration", "executables"] {
                for bad_value in [json!(0), json!(33), json!(97), json!("3"), json!(null)] {
                    let mut bad = input.clone();
                    bad["submods"][device]["ear.trustworthiness-vector"][field] = bad_value;
                    assert!(!allowed(&policy, tag, &bad));
                    cases += 1;
                }
            }
        }
    }
    let mut legacy = input.clone();
    legacy["submods"]["cpu0"]["ear.veraison.annotated-evidence"]["init_data"] = json!("legacy");
    legacy["submods"]["cpu0"]["ear.veraison.annotated-evidence"]["snp"]["measurement"] =
        json!("legacy");
    assert!(allowed(&policy, "edge-model-key", &legacy));
    cases += 1;
    legacy["submods"].as_object_mut().unwrap().remove("gpu0");
    assert!(!allowed(&policy, "edge-model-key", &legacy));
    cases += 1;
    println!("PASS {cases} scoped resource-policy cases; synthetic claims only");
}
