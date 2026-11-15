# Optional research companion

The **research companion** preserves the older native Windows interface with the NASA ECG fitted-model demonstration and historical-reference controls. Use the V11 app for the current main experience. Use this separate interface to inspect capabilities that have not been ported to React.

Download **[BioTwinSync-0.1.0-windows-x64-research-companion.zip](https://github.com/calavera16/BioTwin-Sync/releases/download/v0.1.0/BioTwinSync-0.1.0-windows-x64-research-companion.zip)**. Extract the whole ZIP into its own folder, open **BioTwinSync**, then run **BioTwinSync.exe**. Python and Tk are included; no installation, account or key is needed. The portable prototype is unsigned. The same native tools are also available from source with `python run_desktop.py` (Python 3.13 with Tk).

## Try actual NASA ECG model inference

1. Close the initial quick guide, or finish its four steps.
2. Open **Session → Local measurements**.
3. Select **Signals → Open separate model demo**.
4. Choose **Load NASA ECG example**. The source identity and original timing accompany the trace; the fitted forest scores appear in the analysis.
5. Choose **Create labelled corrupted demo** to compare a deliberate sample-hold alteration. This is a controlled artificial disruption, not a second NASA recording or a medical event.
6. Inspect the report, mark a segment or queue a repeat request, then **Export demo report**. The report excludes raw sample arrays.

The bundled source example comes from a training participant. Running it demonstrates reproducible inference, not fresh validation. Both forest outputs remain experimental; the natural author-exclusion model failed its held-out gate. Neither output selects the assistant's guidance or changes crew health readings.

## Inspect historical reference tools

Open a crew monitor, expand **Earth review**, then choose **References**. The reference library supports local WinSCAT files and an explicit **Fetch NASA** action for 26 allowlisted source files. Fetching needs Internet access; the bundled model demo works offline. Raw WinSCAT records are not redistributed in this package. Historical cases are separate research participants, not matched patients or current crew members.

## What this package contains

- The unchanged **ux-20261114-n** native runtime, originally built November 14, 2026.
- Three attributed original NASA SOTERIA excerpts and the two trained forest heads.
- Existing measurement, assistant, consultation, history and session tools from that older interface.
- Project MIT license, AI-use disclosure, data attribution and original dependency notices.

This package does not add a graphical interface for the later ECG count-reliability or four-policy evidence-link experiments. Their implementations and results are described in the [technical tour](JUDGES.md) and [research evidence](evidence/README.md).

The original 983 archive entries are retained byte-for-byte. The new outer package adds only documentation and notices. Its original archive SHA-256 is `00bb830a63491bbe4a219fb11e2c97992ac1922f33d6ee87ccee4cd9067bd9ef`; use the separately supplied `SHA256SUMS.txt` for the new companion ZIP.

A fresh same-machine smoke run on November 15 passed all 24 checks, including trained-model inference, session round-trip, role isolation and native control construction. This is automated software verification, not a manual usability study, second-device check, clinical validation or flight qualification.

[Return to the judge tour](JUDGES.md) · [Model evidence and reproduction](evidence/README.md)
