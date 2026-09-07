# Attribution for MaRDI Ontologies and Data

This project incorporates ontological structure and (optionally) reference data from the Mathematical Research Data Initiative (MaRDI / NFDI). All MaRDI artifacts are licensed **CC-BY 4.0**.

## Ontologies

### MathModDB — Ontology and Knowledge Graph for Mathematical Models
- Version aligned to: **2.0.0** (released 2026-07-28)
- Repository: https://github.com/MaRDI4NFDI/MathModDB
- Docs: https://mardi4nfdi.github.io/MathModDB/
- Citation: Shehu, A., Schembera, B., Schmidt, B., Biedinger, C., Fiedler, J., Reidelbach, M., Koprucki, T. (2025): *MathModDB – An Ontology and Knowledge Graph for Mathematical Models.* https://doi.org/10.5281/zenodo.14887915
- License: CC-BY 4.0
- Local copies: `docs/mardi/MathModDB.owl`

### MathAlgoDB — Algorithm Knowledge Graph Ontology
- Version: 0.1 (MaRDI TA2, 2022)
- Repository: https://github.com/MaRDI4NFDI/MathAlgoDB
- Docs/frontend: https://mardi4nfdi.github.io/MathAlgoDB/ and https://mathalgodb.mardi4nfdi.de/
- Citation: Himpe, C., Wübbeling, F., Kleikamp, H., Fritze, R., Rave, S. (2022): *MaRDI Task Area 2 – Scientific Computing @ WWU Münster. AlgoData – Algorithm Knowledge Graph – Ontology (Version 0.1).* https://mardi4nfdi.de/algodata/0.1
- License: CC-BY 4.0
- Local copies: `docs/mardi/MathAlgoDB_Ontology.ttl`, `docs/mardi/MathAlgoDB_Data.ttl`

## Papers (background, cited in plan)

- Schembera, B., Wübbeling, F., Kleikamp, H., Biedinger, C., Fiedler, J., Reidelbach, M., Shehu, A., Schmidt, B., Koprucki, T., Iglezakis, D., Göddeke, D. (2024): *Ontologies for Models and Algorithms in Applied Mathematics and Related Disciplines.* MTSR 2023, CCIS vol 2048. https://doi.org/10.1007/978-3-031-65990-4_14 (arxiv:2310.20443)
- Schembera, B., Wübbeling, F., Kleikamp, H., Schmidt, B., Shehu, A., Reidelbach, M., Biedinger, C., Fiedler, J., Koprucki, T., Iglezakis, D., Göddeke, D. (2025): *Towards a Knowledge Graph for Models and Algorithms in Applied Mathematics.* MTSR 2024. https://doi.org/10.1007/978-3-031-81974-2_8

## Data-import attribution rule

Any node imported from MaRDI sources must carry:
- `origin = "mardi"`
- `sources` containing the MaRDI Portal entity IRI (e.g. `https://portal.mardi4nfdi.de/entity/Q68663`)
- `wikidata_qid` where provided by the source entity

This file + per-node fields together satisfy CC-BY 4.0 attribution if `kb-data` is ever published.
