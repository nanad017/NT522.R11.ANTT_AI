# Raw dataset card

- Cohort: temporal AndroZoo 10k
- Samples: 10,000 APK SHA-256 identifiers
- Train: 8,422 APKs from 2019–2021
- Validation: 600 APKs from 2022
- Test: 978 APKs from 2023–2026
- Labels: `benign=0`, `malware=1`
- Split policy: temporal and package-disjoint; never random-split again
- Selection seed: recorded per row in `final-manifest.csv`
- Raw APK distribution: not included
- Reproduction key: SHA-256 in the locked manifest

The processed compact ML/FCG cohort contains 9,855 usable samples. The 145
declared exclusions are recorded in `../processed/metadata/exclusions.parquet`.
