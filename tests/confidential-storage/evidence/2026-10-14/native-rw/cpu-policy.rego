package policy

import rego.v1

default executables := 33
default hardware := 97
default configuration := 36

executables := 3 if {
    input.snp.measurement == "a248eb38d39e9027b866cb6b6ef86e48fb13eabace3d69d56d71e50872bc769f46df57a95ce04635bc37c9716f12d49e"
}

hardware := 2 if {
    input.snp.reported_tcb_bootloader == 3
    input.snp.reported_tcb_tee == 2
    input.snp.reported_tcb_snp == 6
    input.snp.reported_tcb_microcode == 117
}

# SMT is explicitly acceptable for this diagnostic. TSME is not required:
# SNP protects the guest's private memory independently of host-wide TSME.
# ABI 0.0, SMT allowed, no migration/debug and no single-socket requirement
# correspond to the pinned handler's policy=196608. No weakened fallback.
configuration := 2 if {
    input.snp.policy_abi_major == 0
    input.snp.policy_abi_minor == 0
    input.snp.policy_smt_allowed == true
    input.snp.policy_migrate_ma == false
    input.snp.policy_debug_allowed == false
    input.snp.policy_single_socket == false
    input.init_data == "737703c5aa5ce16867226ab85e1f36719d512a3e4b9f30d90568af9a44e81c09"
}

trust_claims := {
    "executables": executables,
    "hardware": hardware,
    "configuration": configuration,
    "file-system": 0,
    "instance-identity": 0,
    "runtime-opaque": 0,
    "storage-opaque": 0,
    "sourced-data": 0,
}
