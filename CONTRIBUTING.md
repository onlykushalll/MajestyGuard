# Contributing

Thanks for taking MajestyGuard seriously. This project touches privacy, biometrics, Windows services, and lock-screen research, so changes need extra care.

## Development Rules

- Keep biometric processing local.
- Do not commit enrollment data, model files, local logs, credentials, or user config.
- Do not lower recognition or liveness thresholds just to make a test pass.
- Do not enable machine-level install behavior by default.
- Keep installer changes paired with uninstall or rollback behavior.
- Keep Credential Provider and service work opt-in.

## Local Validation

Run focused tests before opening a pull request:

```powershell
dotnet test .\src\MajestyGuard.Tests\MajestyGuard.Tests.csproj
```

For Python CV work, use non-camera tests where possible. Live camera testing must be bounded by time and should not enable real lock behavior unless explicitly planned.

## Pull Requests

Include:

- what changed
- how it was tested
- any security, privacy, or installer impact
- whether the change affects service, Credential Provider, or lock behavior

Small, reviewable pull requests are strongly preferred.
