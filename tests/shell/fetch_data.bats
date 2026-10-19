#!/usr/bin/env bats
# Offline checks of scripts/fetch_data.sh: files already present are reused,
# and a checksum mismatch stops the script. Nothing is downloaded.

setup() {
    SCRIPT="${BATS_TEST_DIRNAME}/../../scripts/fetch_data.sh"
    DATA="${BATS_TEST_TMPDIR}/data"
    mkdir -p "${DATA}/clinvar" "${DATA}/pipeprof/bam"
    printf 'not really a vcf\n' > "${DATA}/clinvar/clinvar.vcf.gz"
    printf 'index\n' > "${DATA}/clinvar/clinvar.vcf.gz.tbi"
    printf 'bam\n' > "${DATA}/pipeprof/bam/NA12878.chr20.lowcov.bam"
}

md5_of() {
    if command -v md5sum >/dev/null 2>&1; then md5sum "$1" | awk '{print $1}'; else md5 -q "$1"; fi
}

@test "reuses present files and prints their SHA-256" {
    echo "$(md5_of "${DATA}/clinvar/clinvar.vcf.gz")  clinvar.vcf.gz" > "${DATA}/clinvar/clinvar.vcf.gz.md5"
    run "${SCRIPT}" "${DATA}"
    [ "$status" -eq 0 ]
    [[ "$output" == *"reuse"* ]]
    [[ "$output" == *"md5 ok"* ]]
    [[ "$output" == *"NA12878.chr20.lowcov.bam"* ]]
}

@test "stops on an MD5 mismatch" {
    echo "00000000000000000000000000000000  clinvar.vcf.gz" > "${DATA}/clinvar/clinvar.vcf.gz.md5"
    run "${SCRIPT}" "${DATA}"
    [ "$status" -eq 1 ]
    [[ "$output" == *"MD5 mismatch"* ]]
}
