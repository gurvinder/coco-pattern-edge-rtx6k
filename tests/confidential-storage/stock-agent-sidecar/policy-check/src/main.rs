use std::{
    fs,
    num::{NonZeroU32, NonZeroUsize},
};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args().collect();
    let mut engine = regorus::Engine::new();
    engine.set_strict_builtin_errors(false);
    engine.set_gather_prints(true);
    engine.set_policy_length_config(regorus::PolicyLengthConfig {
        max_col: NonZeroU32::new(64 * 1024).unwrap(),
        max_file_bytes: NonZeroUsize::new(16 * 1024 * 1024).unwrap(),
        max_lines: NonZeroUsize::new(200_000).unwrap(),
    });
    engine.add_policy("sidecar.rego".into(), fs::read_to_string(&args[1])?)?;
    let cases: serde_json::Value = serde_json::from_str(&fs::read_to_string(&args[2])?)?;
    for case in cases.as_array().ok_or("cases must be an array")? {
        engine.set_input_json(&case["input"].to_string())?;
        let query = format!("data.agent_policy.{}", case["endpoint"].as_str().ok_or("endpoint")?);
        let result = engine.eval_rule(query)?;
        let expected = regorus::Value::Bool(case["allowed"].as_bool().ok_or("expected boolean")?);
        if result != expected {
            return Err(format!("case {}: expected {expected}, got {result}", case["name"]).into());
        }
        println!("PASS {}", case["name"]);
    }
    Ok(())
}
