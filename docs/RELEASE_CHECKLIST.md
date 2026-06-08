# Release Checklist

Use this checklist before publishing a MajestyGuard release or requesting code signing.

## Source Readiness

- [ ] All release source is committed.
- [ ] Public repository contains current source.
- [ ] `README.md` reflects the current release state.
- [ ] `PRIVACY.md` is current.
- [ ] `CODE_SIGNING_POLICY.md` is current.
- [ ] Third-party licenses are documented.

## Build And Test

- [ ] `.NET` tests pass.
- [ ] Python non-camera checks pass.
- [ ] Installer copy-only smoke test passes.
- [ ] Uninstaller dry-run or safe validation passes.
- [ ] No generated logs, model files, embeddings, or local config are included.

## Safety Review

- [ ] Machine-level changes are documented.
- [ ] Credential Provider registration is opt-in.
- [ ] Real lock behavior is opt-in.
- [ ] Recovery and uninstall paths are documented.
- [ ] Smart App Control is not disabled as part of the release process.

## Signing

- [ ] Signing request maps to a public commit.
- [ ] Release artifacts are built by the documented workflow.
- [ ] Product name and version metadata are consistent.
- [ ] Release approver manually approves signing.
- [ ] Release notes mention signing status honestly.
