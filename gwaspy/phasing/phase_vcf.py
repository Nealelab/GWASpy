__author__ = 'Lindo Nkambule'

import hailtop.batch as hb
import hail as hl
import pandas as pd
from gwaspy.phasing.get_filebase import get_vcf_filebase
from gwaspy.utils.get_file_size import bytes_to_gb
from typing import Union


def eagle_phasing(b: hb.batch.Batch,
                  vcf_file: str = None,
                  ref_vcf_file: str = None,
                  reference: str = 'GRCh38',
                  cpu: int = 8,
                  img: str = 'docker.io/lindonkambule/gwaspy:v1',
                  threads: int = 16,
                  out_dir: str = None):
    global ref_size, vcf_ref
    vcf_size = bytes_to_gb(vcf_file)
    if ref_vcf_file:
        ref_size = bytes_to_gb(ref_vcf_file)
        vcf_ref = b.read_input(ref_vcf_file)
    mem = 'highmem' if vcf_size > 1 else 'standard'
    disk_size = round(5.0 + 3.0 * vcf_size) + round(5.0 + 3.0 * ref_size) if ref_vcf_file else round(5.0 + 3.0 * vcf_size)

    vcf_filename_no_ext = get_vcf_filebase(vcf_file)
    output_file_name = f'{vcf_filename_no_ext}.phased.eagle'
    vcf = b.read_input(vcf_file)

    map_file = '/opt/genetic_map_hg38_withX.txt.gz' if reference == 'GRCh38' else '/opt/genetic_map_hg19_withX.txt.gz'

    phase = b.new_job(name=output_file_name)
    phase.cpu(cpu)
    phase.memory(mem)
    phase.storage(f'{disk_size}Gi')
    phase.image(img)

    if ref_vcf_file:
        cmd = f'''
        eagle \
            --geneticMapFile {map_file} \
            --numThreads {threads} \
            --outPrefix {output_file_name} \
            --vcfOutFormat b \
            --vcfRef {vcf_ref} \
            --vcfTarget {vcf}
        '''

    else:
        cmd = f'''
        eagle \
            --geneticMapFile {map_file} \
            --numThreads {threads} \
            --outPrefix {output_file_name} \
            --vcfOutFormat b \
            --vcf {vcf}
        '''

    phase.command(cmd)

    phase.command(f'mv {output_file_name}.vcf.gz {phase.ofile}')
    b.write_output(phase.ofile, f'{out_dir}/{output_file_name}.vcf.gz')

    return phase


def shapeit_phasing(b: hb.batch.Batch,
                    vcf_file: str = None,
                    ref_vcf_file: str = None,
                    reference: str = 'GRCh38',
                    region: Union[str, int] = None,
                    map_chromosome: str = None,
                    cpu: int = 4,
                    img: str = 'docker.io/lindonkambule/gwaspy:v1',
                    threads: int = 3,
                    out_dir: str = None):

    global ref_size, vcf_ref
    vcf_size = bytes_to_gb(vcf_file)
    if ref_vcf_file:
        ref_size = bytes_to_gb(ref_vcf_file)
        vcf_ref = b.read_input(ref_vcf_file)
    mem = 'highmem' if vcf_size > 1 else 'standard'
    disk_size = round(5.0 + 3.0 * vcf_size) + round(5.0 + 3.0 * ref_size) if ref_vcf_file else round(5.0 + 3.0 * vcf_size)

    vcf_filename_no_ext = get_vcf_filebase(vcf_file)
    output_file_name = f'{vcf_filename_no_ext}.phased.shapeit.bcf'
    vcf = b.read_input(vcf_file)

    map_file = f'/shapeit4/maps/b38/{map_chromosome}.b38.gmap.gz' if reference == 'GRCh38'\
        else f'/shapeit4/maps/b37/{map_chromosome}.b37.gmap.gz'

    phase = b.new_job(name=output_file_name)
    phase.cpu(cpu)
    phase.memory(mem)
    phase.storage(f'{disk_size}Gi')
    phase.image(img)

    if ref_vcf_file:
        # shapeit requires that the VCF be indexed
        phase.command(f'bcftools index {vcf_ref}')
        cmd = f'''
        shapeit4.2 \
            --input {vcf} \
            --map {map_file} \
            --region {region} \
            --reference {vcf_ref} \
            --output {output_file_name} \
            --thread {threads}
        '''

    else:
        cmd = f'''
        shapeit4.2 \
            --input {vcf} \
            --map {map_file} \
            --region {region} \
            --output {output_file_name} \
            --thread {threads}
        '''

    phase.command(f'bcftools index {vcf}')
    phase.command(cmd)

    phase.command(f'mv {output_file_name} {phase.ofile}')
    b.write_output(phase.ofile, f'{out_dir}/{output_file_name}')

    return phase


def run_phase(backend: Union[hb.ServiceBackend, hb.LocalBackend] = None,
              input_vcfs: str = None,
              vcf_ref_path: str = None,
              software: str = 'shapeit',
              reference: str = 'GRCh38',
              cpu: int = 4,
              threads: int = 3,
              out_dir: str = None):

    # error handling
    if software.lower() not in ['eagle', 'shapeit']:
        raise SystemExit(f'Incorrect software {software} selected. Options are [eagle, shapeit]')

    if reference not in ['GRCh37', 'GRCh38']:
        raise SystemExit(f'Incorrect reference genome build {reference} selected. Options are [GRCh37, GRCh38]')

    phasing = hb.Batch(backend=backend,
                       name=f'haplotype-phasing-{software}')

    if vcf_ref_path:
        print('RUNNING PHASING WITH A REFERENCE PANEL\n')
    else:
        print('RUNNING PHASING WITHOUT A REFERENCE PANEL\n')

    vcf_paths = pd.read_csv(input_vcfs, sep='\t', header=None)

    # get the regions so we can map each file to its specific region
    regions = pd.read_csv(f'{out_dir}/GWASpy/Phasing/regions.lines', sep='\t', names=['reg', 'ind'])
    regions_dict = pd.Series(regions.reg.values, index=regions.ind).to_dict()

    for index, row in vcf_paths.iterrows():
        vcf = row[0]
        vcf_filebase = get_vcf_filebase(vcf)
        scatter_vcfs_paths = hl.utils.hadoop_ls(f'{out_dir}/GWASpy/Phasing/{vcf_filebase}/scatter_vcfs')

        vcfs = []
        for i in scatter_vcfs_paths:
            vcfs.append(i['path'])

        phased_vcf_out_dir = f'{out_dir}/GWASpy/Phasing/{vcf_filebase}/phased_scatter'

        for file in vcfs:
            # get specific region for file using regions.line file
            vcf_basename = get_vcf_filebase(file)
            file_index = int(vcf_basename.split('.')[-1])
            file_region = regions_dict[file_index]
            map_chrom = file_region.split(':')[0]

            if software == 'eagle':
                eagle_phasing(b=phasing, vcf_file=file, ref_vcf_file=vcf_ref_path, reference=reference, cpu=cpu,
                              threads=threads, out_dir=phased_vcf_out_dir)

            else:
                shapeit_phasing(b=phasing, vcf_file=file, ref_vcf_file=vcf_ref_path, reference=reference,
                                region=file_region, map_chromosome=map_chrom, cpu=cpu, threads=threads,
                                out_dir=phased_vcf_out_dir)

    phasing.run()

