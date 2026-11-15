# Sources, transformations and licenses

The original project license does not replace any third-party license. Public availability alone is not treated as permission to redistribute every NASA-hosted dataset. No NASA logo, endorsement, clinical approval or astronaut disease labels are claimed.

## NASA resources and actual use

- [Human Research Program](https://www.nasa.gov/hrp/), [distance from Earth](https://www.nasa.gov/hrp/hazard-distance-from-earth/) and [human spaceflight hazards](https://www.nasa.gov/ochmo-hmta-human-spaceflight-hazards/): motivate delayed review, local support and the illustrative 20-minute one-way scenario. The fixed scenario omits review time; it is not a live link measurement.
- [NASA remote health-monitoring research announcement](https://www.nasa.gov/hrp/researchers-apply-now-with-your-ideas-to-advance-remote-health-monitoring-in-space/): project motivation and source-linked assistant background, not validation of this software.
- [NASA crew interfaces reference](https://www.nasa.gov/reference/10-0-crew-interfaces-vol-2/): information and human-review context.

The selected challenge is [Health Monitoring Software](https://www.spaceappschallenge.org/2026/challenges/health-monitoring-software/?tab=resources). A [NASA Space Apps Navigator discussion](https://github.com/nasa/spaceapps/discussions/2133) recommends HRP for this challenge. The challenge Resources tab was unavailable during automated verification; the team must confirm its exact required-resource mapping in the official form. Relevance is not a claim that this eligibility check has already passed.

## Bundled NASA SOTERIA excerpts

NASA Langley Research Center / System-Wide Safety Project, [NASA SOTERIA Simulation Testbed Data](https://registry.opendata.aws/nasa-soteria-data/), accessed 2026-11-14. Registry license: [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).

Fettrow, T. and colleagues (2025), *Human Contributions to Safety Data Testbed Flight Simulation Study: Data Methods, Processing, and Quality*, Scientific Data 12, 1247. [doi:10.1038/s41597-025-05336-7](https://doi.org/10.1038/s41597-025-05336-7).

Three derived ECG/BVP JSON excerpts retain original parsed samples, independent source clocks, gaps and original sample offsets. Display-only min/max decimation retains selected original extrema; it is not a measured compression result. Amplitude units and channel synchronization remain unverified. Pilots in terrestrial flight simulations are distinct from the fictional workspace crew. Annotation marks describe the authors' detector exclusions, not diagnoses or complete artifact truth.

[Exact transformations and attribution](assets/desktop/ATTRIBUTION.txt) · [excerpt integrity manifest](assets/desktop/manifest.json) · [source-object manifest](data/download-manifest.json).

[NASA reference processing code](https://github.com/nasa/nasa-soteria-processing-code) was consulted for annotation semantics at commit `f8661c015346db2ef8895c91be3d4734519a97a4`. Its custom notices are separate from the dataset license. Its scripts are not redistributed in this project.

## NASA NLSP / WinSCAT

[WinSCAT Campaign 3](https://nlsp.nasa.gov/search/data/details/331549a1-8928-52d6-8bb7-00df9ecb1e55?dataType=DATASETS) informed cognitive research parsing and historical-reference handling: 26 CSV files, 272 assessments from 26 source identities. [BEDREST0543 Campaign 3](https://nlsp.nasa.gov/search/data/details/0f2faf2f-5c58-5214-93c7-a8d23c77b3ba?dataType=DATASETS) remains separate review-only physiological research. Accessed 2026-11-14.

The [NLSP catalog](https://data.nasa.gov/dataset/nasa-life-sciences-portal-nlsp) did not specify a redistribution license. Raw NLSP files are excluded from both the public source and release. Only source metadata and an explicit optional bounded downloader remain. Preserve phase, method, source dates and unverified reaction-time units; historical research is not validation of the synthetic mission. [Source manifest](data/nlsp-download-manifest.json).

## Assistant background

The small versioned library contains original paraphrases and source metadata, not full articles. [NHS headaches](https://www.nhs.uk/symptoms/headaches/), [shortness of breath](https://www.nhs.uk/symptoms/shortness-of-breath/), [tiredness and fatigue](https://www.nhs.uk/symptoms/tiredness-and-fatigue/) and [FDA Pulse Oximeter Basics](https://www.fda.gov/consumers/consumer-updates/pulse-oximeter-basics) provide terrestrial background, accessed 2026-11-14. The fatigue page's review-due date had passed and is retained in source metadata. No clinical review, SpO2 acquisition, prescribing or mission clearance is claimed. These sources are not covered by SOTERIA's license.

## Experimental models

SOTERIA-derived data-only forest weights retain the dataset attribution and transformation provenance. The natural author-exclusion target failed; the controlled synthetic-corruption score is not natural-artifact or health accuracy. No learned clinical acceptance is enabled.

The separate count-error experiment uses George Moody and Roger Mark's [MIT-BIH Arrhythmia Database v1.0.0](https://physionet.org/content/mitdb/1.0.0/) and [MIT-BIH Noise Stress Test Database v1.0.0](https://physionet.org/content/nstdb/1.0.0/), accessed 2026-11-14, under [Open Data Commons Attribution v1.0](https://opendatacommons.org/licenses/by/1-0/). Citations: Moody & Mark, IEEE Engineering in Medicine and Biology 20(3):45-50 (2001); Moody, Muldrow & Mark, Computers in Cardiology 11:381-384 (1984); dataset DOIs [C2F305](https://doi.org/10.13026/C2F305) and [C2HS3T](https://doi.org/10.13026/C2HS3T). Raw recordings are excluded. Independent detector features and data-only coefficients retain experiment provenance. The overall gate failed; learned acceptance is disabled. NASA-domain inputs were unavailable under the fixed sampling checks. The subsequent simulated-link results demonstrate transport behavior only.

## Software, visuals and runtime

- Python 3.13.17: PSF/Python notices. PyInstaller 6.22.3: GPL with its distribution exception; its license is included. [Backend runtime notices](assets/desktop/RUNTIME-NOTICES.txt). These historical notices also cover Tcl/Tk used by the separate native interface.
- Electron 45.3.2: MIT, with Chromium and other third-party notices included in the Windows package's `LICENSE`, `LICENSES.chromium.html` and `licenses` folder. [Electron source and license](https://github.com/electron/electron). The root `BioTwinSync/LICENSE` in the portable package is Electron's license; original-project MIT terms are separately supplied under `SUBMISSION-NOTICES/LICENSE`.
- React, ReactDOM and scheduler: MIT; exact notices in [frontend notices](assets/presentation/THIRD-PARTY-NOTICES.txt). Esbuild is a development tool under MIT. Versions are pinned in the frontend package lock.
- Optional NumPy, SciPy, scikit-learn, Matplotlib and WFDB dependencies are for development/training, not bundled application dependencies; their upstream licenses apply when installed. [Backend lock](requirements-backend.lock.txt), [research lock](requirements-reliability.lock.txt), [build lock](requirements-build.lock.txt).
- The team supplied an earlier Vitalnauts interface concept used for crew/role navigation and communication presentation; hard-coded clinical claims, personal vital-sign images and local broadcast logic were excluded. Team source contributions fall under the chosen original-project license, subject to contributor rights.
- App diagrams and icon are project-authored code/SVG with AI assistance. App screenshots show the actual prototype with research or synthetic examples. The slide cover's abstract illustration was generated with OpenAI image generation and is visibly labeled. No NASA logo is used. See [AI disclosure](AI_USE.md).
