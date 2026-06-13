# WHCDF secondaryAuthenticationFactor Capability Request Draft

To: cdfonboard@microsoft.com

Subject: Request for secondaryAuthenticationFactor capability onboarding - MajestyGuard

Hello Microsoft Companion Device Framework onboarding team,

I would like to request guidance and approval for using the restricted
secondaryAuthenticationFactor capability for a Windows Hello companion device app.

Project name: MajestyGuard

Developer/applicant: [your full legal name]

Developer account / Partner Center account: [your Microsoft developer account email]

Company or publisher name: [individual / company name]

Target platform: Windows 11, UWP companion app plus local Windows service/CV components

Capability requested: secondaryAuthenticationFactor

Project summary:

MajestyGuard is a local-only Windows security system that uses face recognition,
passive liveness checks, and a companion-device style authentication flow to
protect a user's Windows profile. The intended WHCDF companion app would use the
Windows.Security.Authentication.Identity.Provider SecondaryAuthenticationFactor
APIs and HMAC-based challenge/response to authorize unlock only when the enrolled
user is freshly recognized with liveness.

Security notes:

- All face recognition and liveness inference is local-only.
- Camera frames are not uploaded.
- The companion flow uses HMAC challenge/response, not plaintext secrets.
- The app is being developed with explicit rollback and recovery procedures
  before any lock-screen integration.

Could you please confirm whether WHCDF / secondaryAuthenticationFactor onboarding
is still available for new apps, and what documentation, Partner Center account
details, package identity, hardware/security review materials, or Store submission
requirements are needed for approval?

Thank you,

[your full legal name]
