# ISOLATED DIAGNOSTIC ONLY. This image/InitData is never approved for keys.
# Reference is independently reproduced by measure.py from pinned boot inputs.
# The old recorded CPU baseline supplies the TCB requirements. This is not an
# assessment of current vendor advisories or approval for production storage.
package policy

import rego.v1

default executables := 33
default hardware := 97
default configuration := 36

executables := 3 if {
    input.snp.measurement == "803bb809924482646eca4d535c9cb382b245aa076387f2e963f833b6f8dc27cc67fa642fe54a9b4f796ccb1906c46d68"
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
    input.init_data == "e2a4bfe74cf95d85efe1d72e63dec8aec51a0d3c7e8f322070a123d0b95d6cf0"
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
