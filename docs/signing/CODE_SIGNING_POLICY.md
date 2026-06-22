# Code Signing Policy

This document is the Code signing policy for MajestyGuard.

## Purpose

MajestyGuard integrates with Windows security-sensitive areas such as services, process state, camera-based presence detection, and Credential Provider research. Signed release artifacts are required so users and reviewers can verify origin and integrity without weakening Windows security settings.

## Signing Goals

- Release artifacts should be built from the public source repository.
- Signing should happen through an auditable CI/release process.
- Signing keys should not be stored in the repository.
- Signed binaries should use consistent product metadata.
- Signing should not be used for private, unverifiable, or locally modified binaries.

## Preferred Signing Route

The preferred open-source route is:

Free code signing provided by SignPath.io, certificate by SignPath Foundation.

MajestyGuard is preparing to apply for SignPath Foundation signing. Until that approval is complete, signed production releases should not be claimed.

## Roles

Current project roles:

- Maintainer: Kushal
- Release approver: Kushal
- Build/release automation: GitHub Actions, planned

These roles may be updated as the project gains contributors.

## What May Be Signed

Only MajestyGuard release artifacts built from this repository should be signed, including:

- Windows service binaries
- overlay binaries
- Credential Provider binaries
- installers or release packages

Upstream third-party binaries should not be re-signed as MajestyGuard binaries. If third-party components are bundled, their origin and licenses should be documented.

## Required Release Checks

Before requesting signing for a release:

- source changes are committed and pushed
- tests pass
- installer/uninstaller behavior is documented
- privacy policy is current
- release notes describe user-visible changes and system-level behavior
- generated logs, enrollment data, model files, and local configuration are excluded
- signing request is manually approved by the release approver

## Smart App Control

MajestyGuard should not instruct users to disable Smart App Control for production use. If Smart App Control blocks a development build, that is treated as a signing/readiness issue, not as a reason to weaken the user's Windows security posture.
