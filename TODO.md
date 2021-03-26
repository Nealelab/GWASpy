Todo list for preimp_qc (to be renamed since we will be adding other features)
====

## In progress
- [ ] Fixing HWE annotation functions for cases and controls

## Todo

- [x] Update report and preimp_qc.py sections to cater for different data types e.g. case-/control-only and 
case-control data
- [ ] Add filter functions for handling trio dataset (mendel erros for IDs+SNPs and HWE p-value for SNPs) and
update report and preimp_qc.py sections
- [ ] Add support for VCF files and include appropriate filter functions (also
check https://blog.hail.is/whole-exome-and-whole-genome-sequencing-recommendations/)
- [ ] Add argument for doing call rate filtering by site/ancestry
- [ ] Currently, we're saving intermediate files in /tmp/. Work out a way to store these files temporarily