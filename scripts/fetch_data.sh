#!/usr/bin/env bash
# Fetch the public inputs used for the real calibration runs in docs/results.md.
#
# Usage: scripts/fetch_data.sh [DATA_DIR]
# DATA_DIR defaults to ./data (gitignored). The hashes printed at the end are
# the ones recorded in docs/data_sources.md.
set -euo pipefail

DATA_DIR="${1:-${DATA_DIR:-data}}"
mkdir -p "${DATA_DIR}"
DATA_DIR="$(cd "${DATA_DIR}" && pwd)"
CLINVAR_DIR="${DATA_DIR}/clinvar"
BAM_DIR="${DATA_DIR}/pipeprof/bam"
mkdir -p "${CLINVAR_DIR}" "${BAM_DIR}"

# ClinVar GRCh38 VCF. The HTTPS mirror's robots.txt disallows crawlers, so this
# uses NCBI's FTP service, which NCBI documents for bulk downloads. A shared
# copy with a matching .md5 is reused instead of downloading again.
CLINVAR_BASE="ftp://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38"
fetch_clinvar() {
    local name="$1"
    local dest="${CLINVAR_DIR}/${name}"
    if [[ -s "${dest}" ]]; then
        echo "reuse ${dest}"
        return 0
    fi
    local part="${dest}.part.pipeprof.$$"
    curl -sS -m 1800 --retry 3 -o "${part}" "${CLINVAR_BASE}/${name}"
    if [[ -s "${dest}" ]]; then
        rm -f "${part}"
        echo "another process finished ${dest}; reusing it"
    else
        mv "${part}" "${dest}"
    fi
}
fetch_clinvar clinvar.vcf.gz.md5
fetch_clinvar clinvar.vcf.gz
fetch_clinvar clinvar.vcf.gz.tbi

expected_md5="$(awk '{print $1}' "${CLINVAR_DIR}/clinvar.vcf.gz.md5")"
if command -v md5sum >/dev/null 2>&1; then
    actual_md5="$(md5sum "${CLINVAR_DIR}/clinvar.vcf.gz" | awk '{print $1}')"
else
    actual_md5="$(md5 -q "${CLINVAR_DIR}/clinvar.vcf.gz")"
fi
if [[ "${expected_md5}" != "${actual_md5}" ]]; then
    echo "MD5 mismatch for clinvar.vcf.gz: expected ${expected_md5}, got ${actual_md5}" >&2
    exit 1
fi
echo "clinvar.vcf.gz md5 ok (${actual_md5})"

# 1000 Genomes phase 3 low-coverage NA12878 BAM, chromosome 20 slice, fetched by
# region through the index from the AWS Open Data mirror (no robots.txt there).
BAM_URL="https://1000genomes.s3.amazonaws.com/phase3/data/NA12878/alignment/NA12878.mapped.ILLUMINA.bwa.CEU.low_coverage.20121211.bam"
BAM_OUT="${BAM_DIR}/NA12878.chr20.lowcov.bam"
if [[ ! -s "${BAM_OUT}" ]]; then
    (cd "${BAM_DIR}" && samtools view -b -o "${BAM_OUT}.part" "${BAM_URL}" 20 && mv "${BAM_OUT}.part" "${BAM_OUT}")
    samtools index "${BAM_OUT}"
fi

for f in "${CLINVAR_DIR}/clinvar.vcf.gz" "${CLINVAR_DIR}/clinvar.vcf.gz.tbi" "${BAM_OUT}"; do
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "${f}"
    else
        shasum -a 256 "${f}"
    fi
    ls -l "${f}"
done
