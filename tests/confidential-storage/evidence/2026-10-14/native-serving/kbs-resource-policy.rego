package policy
import rego.v1

# Isolated CPU-only storage test. This must never replace the production GPU policy.
expected := {"init_data": "3e51184404f1258d128f3b0b51fc08ce62131d71e7b94f5a1bc14dcfc8ed04d6", "measurement": "b1c7539897de7c630afb70b48f2f9996c3d742e740d86cbc9216a827173ddfc1662e4008832cba0dfdb8760418dde276", "tcb": {"bootloader": 3, "microcode": 117, "snp": 6, "tee": 2}}
default allow := false

allow if {
    data.plugin == "resource"
    data["resource-path"] == ["storage-test", "model-keys", "qwen-luks-v1"]
    count(data.query) == 0
    input.iss == "storage-kbs-as"
    count(input.submods) == 1
    cpu := input.submods.cpu0
    cpu["ear.status"] == "affirming"
    cpu["ear.appraisal-policy-id"] == "default"
    cpu["ear.trustworthiness-vector"].executables == 3
    cpu["ear.trustworthiness-vector"].hardware == 2
    cpu["ear.trustworthiness-vector"].configuration == 2
    evidence := cpu["ear.veraison.annotated-evidence"]
    evidence.init_data == expected.init_data
    evidence.snp.measurement == expected.measurement
    evidence.snp.reported_tcb_bootloader == expected.tcb.bootloader
    evidence.snp.reported_tcb_tee == expected.tcb.tee
    evidence.snp.reported_tcb_snp == expected.tcb.snp
    evidence.snp.reported_tcb_microcode == expected.tcb.microcode
    evidence.snp.policy_abi_major == 0
    evidence.snp.policy_abi_minor == 0
    evidence.snp.policy_smt_allowed == true
    evidence.snp.policy_migrate_ma == false
    evidence.snp.policy_debug_allowed == false
    evidence.snp.policy_single_socket == false
}
