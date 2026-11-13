# Dependency security exceptions

The CI dependency audit ignores only the two setuptools advisories below. The
same narrow ignore list is applied to the CPU runtime, development, and build
locks because each includes `setuptools==81.0.0`.

| Package | Advisory | Severity | Reason for exception | Review condition |
| --- | --- | --- | --- | --- |
| `setuptools==81.0.0` | `PYSEC-2026-3447` / `GHSA-h35f-9h28-mq5c` / `CVE-2026-59890` | Medium (CVSS 6.1) | The issue is Unicode normalization in source-distribution file exclusions on macOS APFS/HFS+. SARA's supported packaging and CI target is Windows; the affected filesystem behavior is not part of that target. | Reassess if macOS packaging or source-distribution publishing is introduced. |
| `setuptools==81.0.0` | `GHSA-xmj3-9gf7-h52w` | Low | The issue is path traversal through attacker-controlled `namespace_packages.txt` when installing a legacy wheel through setuptools' `install_as_egg` path. The project installs dependencies from reviewed, exact-version locks with pip and does not use that legacy path to process untrusted wheels. | Reassess when the `webrtcvad-wheels`/`pkg_resources` compatibility pin can be removed, or if legacy wheel installation is introduced. |

The `setuptools<82` runtime bound is currently required because the selected
`webrtcvad-wheels` release imports `pkg_resources`, which setuptools 82
removed. This exception does not apply to other setuptools findings or any
other package. CI continues to fail on every unlisted advisory.
