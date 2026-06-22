# Original User Request

## Initial Request — 2026-06-22T17:23:36Z

Sanitize the MajestyGuard repository to remove all AI traces, fix existing Python unit test failures in `test_soft_lock_ui_contract.py`, configure professional open-source documents and templates, and force-push the clean history to GitHub.

Working directory: c:/tmp/MajestyGuard
Integrity mode: development

## Requirements

### R1. AI Trace & Workspace Clean Up
* Remove all AI/agent-specific files, configurations, and folders including `.agents/`, `docs/context/`, `docs/audit_report.md`, and `.github/skills/` from Git tracking and the workspace.
* Update `.gitignore` to prevent any of these files/folders, virtual environments (`.venv/`), models (`models/`), or secrets (`.env`) from being tracked.
* Remove any assistant-specific comments (e.g., references to "Gemini CV" or "Claude bundle") in `daemon/face_engine.py` and `daemon/liveness_detector.py`.

### R2. Professional open-source community standards & templates
* Ensure `LICENSE` contains a standard MIT License.
* Create a standard Contributor Covenant `CODE_OF_CONDUCT.md`.
* Expand `CONTRIBUTING.md` to outline developer setup instructions, coding styles (PEP-8 for Python, standard Microsoft C# styles), and pull request submission guidelines.
* Create standard GitHub issue templates (`.github/ISSUE_TEMPLATE/bug_report.md`, `.github/ISSUE_TEMPLATE/feature_request.md`) and a pull request checklist template (`.github/PULL_REQUEST_TEMPLATE.md`). Remove any old YAML templates and lower-case variants.

### R3. Professional README.md Design
* Update `README.md` to include shields.io badges, a clear "Personal Project" notice, a visualized flattened directory structure tree, a Mermaid.js system architecture flowchart, and a detailed troubleshooting guide for common setup, runtime, and hardware/driver errors.

### R4. Test Suite Verification & Fixes
* Address the `NameError: name '_STATIC_LABEL_OVERRIDES' is not defined` failure in `ui/states.py` by resolving or restoring the missing label override logic to ensure no features or quality are degraded.
* Verify all 320+ Python tests (`pytest daemon/`) and C# tests (`dotnet test`) pass successfully.

### R5. GitHub Repository Push
* Commit all sanitization, formatting, and community documents under clean semantic tags.
* Force-push the cleaned tree to the main remote branch on GitHub (`https://github.com/onlykushalll/MajestyGuard`).

## Acceptance Criteria

### Repository Sanitization & Standards
- [ ] No `.agents/`, `docs/context/`, or AI-assistant references exist in the pushed GitHub history.
- [ ] MIT License, Code of Conduct, expanded contributing guidelines, and issue/PR templates exist.
- [ ] README.md contains badges, Mermaid flowcharts, and the Troubleshooting section.

### Code Integrity
- [ ] The `NameError: name '_STATIC_LABEL_OVERRIDES' is not defined` bug in `ui/states.py` is resolved without degrading features.
- [ ] 100% of Python unit tests (`pytest daemon/`) pass cleanly.
- [ ] 100% of C# unit tests (`dotnet test`) pass cleanly.

### Deployment
- [ ] Git push executes successfully to `github` remote on branch `main` with `--force`.

## Follow-up — 2026-06-22T17:24:31Z

IMPORTANT: The user has updated the instructions. Do NOT delete `.agents/`, `.claude/`, `.gemini/`, `.codex/`, `docs/context/`, `docs/audit_report.md`, `.github/skills/` or any other AI/agent files from the local filesystem on the PC. Keep them in the workspace but ensure they are untracked and excluded via `.gitignore` so they are not pushed to GitHub.
