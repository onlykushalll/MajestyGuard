# Signing and Release Policy

**Status reviewed:** November 29, 2026

## Current Status

MajestyGuard has no production-supported public release and no publisher identity backed by a publicly trusted code-signing certificate. Local development packages may be signed with a project-controlled/self-issued certificate. Such a signature can help detect changes relative to that certificate, but it does not establish a publicly trusted publisher identity and must not be presented as trusted commercial signing.

The active product is the per-user Python/PyQt6 application and installer. The historical Windows service and Credential Provider experiments are not part of the supported release. Do not disable Smart App Control, Code Integrity, Defender, or other Windows protections to install or run a build.

## Release Requirements

Before publishing an installer or announcing a release:

1. Confirm the source commit and review its complete diff, including license and model terms.
2. Run the supported automated suite and the packaging smoke checks; publish exact results and limitations.
3. Test clean install, upgrade, uninstall with data preserved, and uninstall with explicit data removal in a disposable Windows account or VM.
4. Verify model provenance, SHA-256 values, and redistribution permissions. Hash verification is not a license grant.
5. Produce a software bill of materials and include required third-party license notices for all bundled components.
6. Sign with an approved certificate only after the source and reproducible build process are reviewed. Publish the artifact hash and build/source commit.
7. Complete the physical-device gates in the install guide. Do not claim resistance to stranger or presentation attacks without the corresponding live evidence.

## Local Signing

A self-issued development signature is not a substitute for a publicly trusted publisher certificate. Treat local binaries as experimental; obtain them only from a source you trust, verify their hashes, and keep Windows security features enabled. Signing must never be used to disguise modified or unreviewed binaries as an official release.

## Distribution Boundary

The repository license is proprietary and reserves rights. It is not currently eligible to be described as an open-source project on the basis of the repository license. Do not submit applications that claim open-source eligibility or promise third-party signing until the license, ownership, and project status have been resolved by the maintainer.

Generated bundles, installers, smoke state, certificates, keys, and local logs are build outputs and must not be committed to the source repository. Publish a binary only through a reviewed GitHub Release with matching source, checksums, license notices, and safety notes.
