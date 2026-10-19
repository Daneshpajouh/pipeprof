// Split a synthetic VCF by contig, sort and summarise each part, then join them.
// Each command runs under /usr/bin/time -l because Nextflow on macOS records
// no CPU, memory or I/O columns in its trace.

params.vcf = "${projectDir}/raw.vcf.gz"
params.contigs = "c1,c2,c3,c4,c5,c6,c7,c8"
params.outdir = "results"

process SPLIT {
  tag "$contig"
  publishDir "${params.outdir}/usage", pattern: "*.time", mode: 'copy'
  input:
    tuple val(contig), path(vcf)
  output:
    tuple val(contig), path("${contig}.bcf"), emit: part
    path "split_${contig}.time"
  script:
  """
  /usr/bin/time -l bcftools view -t ${contig} -Ou -o ${contig}.bcf ${vcf} 2> split_${contig}.time
  """
}

process SORT {
  tag "$contig"
  publishDir "${params.outdir}/usage", pattern: "*.time", mode: 'copy'
  input:
    tuple val(contig), path(vcf)
  output:
    tuple val(contig), path("${contig}.sorted.vcf.gz"), emit: sorted
    path "sort_${contig}.time"
  script:
  """
  /usr/bin/time -l bcftools sort -T . -Oz -o ${contig}.sorted.vcf.gz ${vcf} 2> sort_${contig}.time
  """
}

process STATS {
  tag "$contig"
  publishDir "${params.outdir}/usage", pattern: "*.time", mode: 'copy'
  input:
    tuple val(contig), path(vcf)
  output:
    path "${contig}.stats.txt"
    path "stats_${contig}.time"
  script:
  """
  /usr/bin/time -l bcftools stats ${vcf} > ${contig}.stats.txt 2> stats_${contig}.time
  """
}

process CONCAT {
  publishDir "${params.outdir}", mode: 'copy'
  input:
    path parts
  output:
    path "joined.vcf.gz"
    path "concat.time"
  script:
  def ordered = params.contigs.tokenize(',').collect { "${it}.sorted.vcf.gz" }.join(' ')
  """
  /usr/bin/time -l bcftools concat -Oz -o joined.vcf.gz ${ordered} 2> concat.time
  """
}

workflow {
  input = Channel.of(params.contigs.tokenize(',')).flatten().map { c -> tuple(c, file(params.vcf)) }
  SPLIT(input)
  SORT(SPLIT.out.part)
  STATS(SORT.out.sorted)
  CONCAT(SORT.out.sorted.map { it[1] }.collect())
}
