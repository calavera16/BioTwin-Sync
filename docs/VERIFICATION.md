# Submission verification — 2026-11-15

These checks describe software behavior and packaging, not clinical validity, flight qualification, measured user benefit or award eligibility.

## Fresh checks on the clean publication source

| Check | Result |
| --- | --- |
| Python release-focused modules | 41 tests passed: mission demo 3, presentation 10, browser host 4, browser workspace 19, desktop build 5 |
| React/frontend tests | 99 passed after installing the pinned development dependencies |
| Electron shell boundary/lifecycle tests | 11 passed |
| Frontend rebuild | Compiled JavaScript, CSS and integrity manifest identical before and after rebuild |
| npm dependency audit during clean install | No known vulnerabilities reported for that installed dependency set; not a complete application security audit |

Commands are in the root README. The storage-capacity test takes roughly two minutes on the verification machine. An initial 100-second test-process limit expired; the full module then passed with an adequate 240-second budget (111.25 seconds actual). An initial frontend test attempt before installing dependencies failed because esbuild was absent; the documented `npm ci --ignore-scripts` setup resolved this. No runtime code was changed to obtain these results.

## Exact downloadable package

`BioTwinSync-0.1.0-windows-x64-v11-submission.zip`

- Size: 168,217,824 bytes.
- SHA-256: `f1e6b9ea49c0325df9e0b0f4f799c793c37fbf71481f04c017ef5e1a08a58eb1`.
- Original V11 ZIP identity: `e41b3aa45a67ff5925378d4b7edda318fb6fb3907262bde53a61ebd73f369e04`.
- All 139 original runtime files remain byte-identical; 138 manifest entries were independently checked. The manifest itself is the remaining file.
- Six source/license/AI notice files were added alongside the app under `SUBMISSION-NOTICES/`; the existing runtime manifest remains intact.
- ZIP CRC integrity and archive paths checked; no raw datasets, session exports, private notes or secret files were included.

After extracting this exact submission ZIP into a fresh directory, all 13 packaged-host checks passed: bounded readiness, frontend assets, crew and doctor state, authentication rejection, recorded workspace start, manual capture, session export, undelivered/delivered role isolation, owned shutdown, clean stdout/stderr and closed loopback listener.

Earlier V11 browser inspection exercised all three example cases, unchanged workspace state, focus/Escape behavior, the original story, 1366×768 and narrow 780-pixel layouts, and the frozen host's actual assets. No JavaScript errors were observed. This browser inspection is distinct from a native Electron/Windows dialog check.

## Publication and presentation checks

- Clean source selection excludes development plans, private project notes, build/cache folders, raw NLSP and MIT-BIH files, local session exports and dependencies installed by npm.
- A bounded text scan found no credential-token/private-key patterns or personal workspace paths in eligible source files. A public dependency-author email in an upstream license is retained as required attribution. `.env` contents were never read, copied, searched or hashed.
- Original-project MIT terms and third-party notices are separate. AI use is acknowledged in prose and JSON metadata, including binary sidecars.
- The final presentation has seven English slides. Its generated cover artwork is visibly labeled; actual application imagery remains identified as a screenshot. PPTX and seven-page PDF were rendered and visually checked; structural/layout checks found no issues. Personal author metadata and local absolute note paths were removed from the public copy.
- The runtime itself is unchanged. Public source package metadata now declares MIT; historical binary package metadata is retained, with current original-project licensing supplied separately.

## Remaining verification

Public repository, PDF and Release download access must be tested while logged out after publishing. The official submission form still needs AI-tool declarations, source explanations and **Submit for Judging** confirmation. The exact challenge Resources-tab match, contributor rights, event timing, registration and attendance require team confirmation.

A separate Windows computer, native Save/Open dialogs and testing with unfamiliar users remain unverified. The package is unsigned, session exports are unencrypted and the prototype has not received clinical, flight or comprehensive security validation. A passing check is evidence for its stated scope only.
