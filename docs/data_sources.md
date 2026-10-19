# Data sources

Raw data lives outside the repository (`scripts/fetch_data.sh DATA_DIR`,
default `./data`, gitignored). Only small test files are in `tests/data`.

| File | Source | Size (bytes) | Fetched (Pacific) | SHA-256 | Terms |
|---|---|---|---|---|---|
| `clinvar.vcf.gz` (release 2026-10-11, server Last-Modified 2026-10-12 22:23 GMT) | ftp://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/clinvar.vcf.gz | 200,437,060 | 2026-10-19 10:59 | `05d9481dd7217cc4df15803362d4a4c9eae5875b27b1750867d6bfd2643b3d10` | public; NCBI asks for attribution to ClinVar as the data source ([ClinVar maintenance and use](https://www.ncbi.nlm.nih.gov/clinvar/docs/maintenance_use/)) |
| `clinvar.vcf.gz.tbi` | same folder | 612,038 | 2026-10-19 10:59 | `1a8e2b66684b4b3f4ca441274777c254f0405e10e304d0e5c08aabeaad8f0dd2` | as above |
| `clinvar.vcf.gz.md5` | same folder; MD5 `0f3a1a5e84252386033d5fcab1bf1f6a` matched | 132 | 2026-10-19 10:59 | n/a | as above |
| `NA12878.chr20.lowcov.bam` | chromosome 20 of https://1000genomes.s3.amazonaws.com/phase3/data/NA12878/alignment/NA12878.mapped.ILLUMINA.bwa.CEU.low_coverage.20121211.bam, fetched by region with `samtools view -b URL 20` (the remote `.bai`, 8,766,440 bytes, is downloaded by htslib) | 310,927,269 | 2026-10-19 10:59 | `48853f2a50abacbc8ecb6c703466aa9fb0260b97a295132c4b3d873bab6ab5fa` | IGSR data use statement ([IGSR disclaimer](https://www.internationalgenome.org/IGSR_disclaimer/)): terms vary by data owner; the phase 3 alignments are distributed openly; AWS Open Data mirror |

Access notes. `https://ftp.ncbi.nlm.nih.gov/robots.txt` disallows all
crawlers, so the HTTPS mirror was not used; the ClinVar files came from NCBI's
FTP service, which NCBI provides for bulk downloads, as a single user-directed
fetch of a named file. The 1000 Genomes EBI FTP site also disallows crawlers
over HTTPS; the AWS Open Data copy has no `robots.txt` (HTTP 404) and was used
instead, reading only the index and the chromosome 20 blocks.

Total downloaded: about 521 MB.
