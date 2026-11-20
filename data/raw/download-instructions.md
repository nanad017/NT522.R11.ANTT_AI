# Reconstructing the raw AndroZoo cohort

Raw APKs are intentionally not distributed in this repository. Use
`final-manifest.csv` as the locked cohort definition. It contains the exact
SHA-256, package, temporal split, label, selection seed/rank, and replacement
metadata for all 10,000 APKs used by the project.

1. Obtain authorized AndroZoo access and keep the API key in an environment
   variable; never commit it.
2. Download each SHA listed in `final-manifest.csv` from the official AndroZoo
   download endpoint.
3. Store it as `<split>/<year>/<label>/<SHA256>.apk`.
4. Calculate SHA-256 after download and require an exact match with the
   manifest before extraction.
5. Alternatively, retrieve the 16 source archives listed in
   `source-archives.csv` from the project-owned remote using the recorded
   object IDs, then verify `source-checksums.sha256`.

The repository contains no AndroZoo API key, OAuth secret, rclone token, APK,
or raw source archive. Access remains subject to AndroZoo's terms.
