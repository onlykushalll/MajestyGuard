# Security Policy

MajestyGuard is security-sensitive software. Please treat reports seriously and avoid posting exploit details publicly until there is a safe fix or mitigation.

## Supported Versions

MajestyGuard is currently an active research prototype. There are no production-supported releases yet.

## Reporting a Vulnerability

For now, report security concerns through GitHub issues with minimal public detail:

https://github.com/onlykushalll/MajestyGuard/issues

If a report involves sensitive local data, credentials, or biometric artifacts, do not attach those files publicly.

## Security Boundaries

MajestyGuard should not:

- store Windows passwords or PINs
- upload biometric templates to cloud services
- require disabling Windows Smart App Control for production use
- silently change system security settings
- register lock-screen components without explicit administrator action

## Development Safety

Use safe development flags when testing live camera or lock behavior. Keep real locking, Credential Provider registration, SafeBoot changes, and registry hardening disabled unless the test has a rollback plan.
