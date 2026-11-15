# BioTwin Sync

**Earth's reply can arrive after the evidence has changed. BioTwin checks the reply against what the crew knows now.**

An offline Windows prototype for the NASA Space Apps 2026 **Health Monitoring Software** challenge. A crew records evidence, sends it through a simulated delay, and receives human-authored conditional instructions. The local evaluator checks those instructions against the latest compatible onboard evidence; stale or missing evidence produces UNKNOWN and withholds the conditional actions.

## Try the app

Open **Releases** on this repository and download **BioTwinSync-0.1.0-windows-x64-v11-submission.zip**. Extract the entire ZIP, open its **BioTwinSync** folder and double-click **BioTwinSync.exe**. Keep all adjacent files and the resources folder together. Windows x64 only; Chromium and Python are included. No account, API key, development tools or Internet connection is needed for the demo. This portable prototype is unsigned.

1. Choose **Watch 30-second example**. Earth reviews 420 ms while the onboard reading changes to 585 ms. The returned authored rule is evaluated against the newer reading.
2. Choose **View example checks**. Fresh 420 ms selects A; fresh 585 ms selects B; stale 585 ms returns UNKNOWN. Inspect the actual reason, freshness and authored steps.
3. Choose **Open workspace**. Record a reading, send a delayed consultation, review the reply and explicitly record completion. Crew and flight surgeon have separate views; Earth sees only delivered evidence.

![Actual prototype showing the delayed-review story](docs/images/mission.png)

[Desktop guide](docs/unified-desktop-guide.md) · [Demo walkthrough](docs/demo-30s.md) · [Verification and limits](docs/VERIFICATION.md) · [Seven-slide presentation](docs/BioTwin-Sync-Submission.pdf)

## What is real, and what is simulated?

| Element | What it demonstrates |
| --- | --- |
| NASA SOTERIA ECG/BVP excerpts | Original samples from terrestrial flight-simulation research, with source timestamps and gaps. These are not astronaut disease labels. |
| NASA NLSP / WinSCAT work | Cognitive research schemas, phase/method distinctions and historical reference handling. Raw records are not redistributed. |
| 420 / 585 ms readings, threshold and 20-minute one-way delay | Explicitly synthetic workflow fixtures and an illustrative threshold. They are separate from the NASA waveforms. |
| A / B / UNKNOWN | Actual deterministic local rule evaluation. Branch names are not health or safety ratings. |

NASA's Human Research Program motivates the distance-from-Earth problem. Real recordings provide an inspectable research-data context; synthetic cases isolate the instruction-selection behavior. Sources, transformations and licenses are in [DATA_SOURCES.md](DATA_SOURCES.md).

## Design

```mermaid
flowchart LR
    A[Crew records evidence] --> B[Simulated delayed delivery]
    B --> C[Human reviewer writes conditional instructions]
    C --> D[Delayed reply]
    D --> E[Check against latest onboard evidence]
    E --> F[Select authored branch or hold as UNKNOWN]
    F --> G[Explicit completion and follow-up]
```

React runs inside bundled Electron/Chromium. A Python standard-library backend owns validation, session state and rule evaluation through an authenticated loopback connection. It is one local computer simulating two roles, not a deployed Earth-to-space network. There is no live generative AI service or telemetry upload. Public source links use the browser only when selected.

## Run the current interface from source

Requires Python 3.13 or newer and a modern browser. Run from the repository root. The compiled frontend is included, so npm is unnecessary for this first run:

```powershell
python -c "from biotwin.presentation import PresentationHost; import webbrowser; h=PresentationHost().start(); webbrowser.open(h.url); input('Press Enter to stop BioTwin: '); h.close()"
```

For frontend development, install Node.js 22 or newer, then:

```powershell
npm --prefix ui/presentation ci --ignore-scripts
npm --prefix ui/presentation test
npm --prefix ui/presentation run build
```

Run the release-focused Python checks separately on Windows:

```powershell
python -m unittest tests.test_mission_demo
python -m unittest tests.test_presentation
python -m unittest tests.test_browser_host
python -m unittest tests.test_browser_workspace
python -m unittest tests.test_desktop_shell_build
node --test ui/desktop/test/*.test.cjs
```

The broader scientific/native test suite includes optional dependencies, display-dependent tests and local research data. It is not the clean-clone quick check. The separate older Tk research interface starts with `python run_desktop.py`.

To build a new Windows package, use a Python 3.13 virtual environment, install `requirements-build.lock.txt`, and run `python scripts/build_desktop_shell.py --tag your-unique-tag`. The builder downloads the pinned official Electron runtime and verifies its checksum. Never reuse an existing build tag. Numerical training dependencies are separate and are not required by the app.

## Limits and next validation

This is a hackathon research prototype, not clinical advice or flight-approved software. No live sensors, diagnosis, prescribing, clinical outcome benefit or security certification is claimed. Browser reaction timing is uncalibrated. Exported session JSON is unencrypted; use fictional data for public demonstrations. Explicitly export before closing to keep recorded work.

Experimental ECG models retain their failed or unavailable gates; they are not promoted as validated health models. The evidence-link experiment established bounded transport behavior, not useful bandwidth savings or policy superiority. A separate Windows computer, native Save/Open dialogs and a study with unfamiliar users remain next validation steps.

## License, attribution and AI use

Original project code and documentation are offered under the [MIT license](LICENSE). Third-party data, dependencies and runtime components retain their own licenses; see [DATA_SOURCES.md](DATA_SOURCES.md) and included notices. NASA, dataset authors and dependency authors do not endorse this project.

AI-assisted implementation, tests, writing and synthetic fixtures are disclosed in [AI_USE.md](AI_USE.md) and machine-readable [AI_USAGE.json](AI_USAGE.json). The presentation's generated illustration is visibly labeled. The declaration also belongs in the official Space Apps submission form.
