# MajestyGuard Use Terms and Limitations

**Last reviewed:** November 29, 2026

These notes describe the intended use and limitations of this project. They do not replace or broaden the rights in [LICENSE.md](LICENSE.md). Third-party libraries, model files, and other bundled components may have separate terms that apply to them.

## Experimental Software

MajestyGuard is an actively developed experimental application. It has not been certified as a security product and is not guaranteed to detect every absence, stranger, spoof, camera failure, or input path. Recognition can fail or take longer depending on the camera, lighting, pose, device load, and enrollment quality. False accepts and false rejects are possible.

The overlay is a user-space layer over an already signed-in Windows session. It is not equivalent to Windows Secure Desktop and cannot guarantee that another process or a sufficiently privileged user cannot bypass it. Keep Windows sign-in, device security, and the Windows lock screen enabled. Use **Win+L** when leaving the PC unattended. Do not rely on MajestyGuard as the only barrier protecting confidential work.

## Responsible Use

Use the software only on a device you own or are authorized to administer. Obtain appropriate agreement before enrolling another person or using a camera where other people may be captured. Do not use the software to secretly monitor people or to make consequential decisions about them.

Review system prompts and the installer before proceeding. Do not disable Windows security features to run a build. Keep backups of important data and maintain a usable Windows sign-in method in case the camera or app fails.

## License and Third-Party Materials

The repository license is currently proprietary and reserves rights; public visibility on GitHub does not itself grant permission to copy, modify, redistribute, or commercially use the project. See [LICENSE.md](LICENSE.md). Obtain model files only from sources whose terms permit your intended use. MajestyGuard does not grant rights to third-party models or dependencies.

## No Warranty or Security Guarantee

The software and documentation are provided as-is, without a promise of uninterrupted operation, accurate recognition, attack prevention, or suitability for a particular purpose. Keep an independent recovery path to Windows. This statement is not legal advice; obtain qualified advice for legal questions about distribution or deployment.

MajestyGuard is an independent project and is not affiliated with Microsoft, Apple, or the makers of any camera or biometric model. Product and company names are used only to describe compatibility or inspiration where relevant.
