// MajestyGuard.CredentialProvider/MajestyCredentialProvider.h
// Custom Windows Credential Provider.
// This is a COM DLL registered in the Windows registry that Winlogon
// loads on the Secure Desktop (Ctrl+Alt+Del / login screen).
//
// ═══════════════════════════════════════════════════════════════════
// HOW CREDENTIAL PROVIDERS WORK:
//   1. Winlogon.exe loads all registered CPs from:
//      HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\
//      Credential Providers\{YOUR-CLSID}
//   2. LogonUI.exe calls ICredentialProvider::SetUsageScenario()
//   3. Your CP returns tile count (1 in our case)
//   4. LogonUI calls ICredentialProviderCredential::GetStringValue() for UI fields
//   5. User interaction → ICredentialProviderCredential::GetSerialization()
//      returns the credential blob to LSA for authentication
//
// OUR APPROACH:
//   - We render minimal Win32 UI (NOT WinUI 3 — Secure Desktop restrictions)
//   - We connect to MajestyGuard Service via Named Pipe
//   - Service sends AuthDecision (granted/denied) based on CV Engine result
//   - If granted: we serialize a fake password credential to bypass the prompt
//     (THIS REQUIRES the account has a password we can submit — see CODEX note)
//   - "Enter Password Instead" button → fall back to standard password field
//
// CRITICAL CODEX NOTE:
//   Credential Providers cannot truly bypass Windows authentication.
//   To "skip" the password, you must either:
//   A) Store the user's password encrypted and submit it on their behalf (risky)
//   B) Use a Virtual Smart Card + face as the unlock mechanism
//   C) Use Windows Hello's existing biometric pipeline (WBF) — RECOMMENDED
//      This is what most face-unlock implementations do.
//   Option C means: implement a Windows Hello companion device provider instead.
//   Discuss this tradeoff with the architecture prompt before coding this CP.
//
// ═══════════════════════════════════════════════════════════════════

#pragma once

#ifndef NTDDI_VERSION
#  define NTDDI_VERSION 0x0A000008
#endif
#ifndef _WIN32_WINNT
#  define _WIN32_WINNT 0x0A00
#endif
#ifndef WIN32_LEAN_AND_MEAN
#  define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#  define NOMINMAX
#endif

#include <windows.h>           // DWORD, BOOL, HMODULE, WCHAR, LPWSTR
#include <guiddef.h>           // GUID, CLSID, DEFINE_GUID, REFCLSID
#include <unknwn.h>            // IUnknown
#include <objbase.h>           // COM infrastructure
#include <credentialprovider.h>// ICredentialProvider, CPFT_*, CPFG_*
#include <wincred.h>           // CredRead, CredWrite
#include <securitybaseapi.h>   // Token APIs
#include <ntsecapi.h>          // KERB types for GetSerialization
#include <string>
#include <atomic>

// ── GUIDs ─────────────────────────────────────────────────────────
// Generate new GUIDs with: uuidgen.exe
// Replace these with YOUR generated GUIDs before registering.
//
// {A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
DEFINE_GUID(CLSID_MajestyCredentialProvider,
    0xa1b2c3d4, 0xe5f6, 0x7890,
    0xab, 0xcd, 0xef, 0x12, 0x34, 0x56, 0x78, 0x90);

// {B2C3D4E5-F6A7-8901-BCDE-F12345678901}
DEFINE_GUID(CLSID_MajestyCredential,
    0xb2c3d4e5, 0xf6a7, 0x8901,
    0xbc, 0xde, 0xf1, 0x23, 0x45, 0x67, 0x89, 0x01);

// ── Field IDs ─────────────────────────────────────────────────────
// These define the UI fields shown in the login tile.
enum MAJESTY_FIELD_ID
{
    FI_STATUS_LABEL   = 0,   // "Verify identity, your majesty"
    FI_FACE_SCAN_IMAGE= 1,   // Animated face scan graphic (bitmap field)
    FI_SUBMIT         = 2,   // Hidden submit button (triggered by auth success)
    FI_FALLBACK_LINK  = 3,   // "Enter password instead" link
    FI_COUNT          = 4,
};

// ── Forward declarations ───────────────────────────────────────────
class CMajestyCredential;

// ═══════════════════════════════════════════════════════════════════
// CMajestyCredentialProvider
// Implements: ICredentialProvider
// Registered as a COM in-proc server (DLL)
// ═══════════════════════════════════════════════════════════════════
class CMajestyCredentialProvider : public ICredentialProvider
{
public:
    CMajestyCredentialProvider();
    ~CMajestyCredentialProvider();

    // IUnknown
    STDMETHODIMP         QueryInterface(REFIID riid, void** ppv) override;
    STDMETHODIMP_(ULONG) AddRef()  override;
    STDMETHODIMP_(ULONG) Release() override;

    // ICredentialProvider
    STDMETHODIMP SetUsageScenario(
        CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus,
        DWORD dwFlags) override;

    STDMETHODIMP SetSerialization(
        const CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* pcpcs) override;

    STDMETHODIMP Advise(
        ICredentialProviderEvents* pcpe,
        UINT_PTR upAdviseContext) override;

    STDMETHODIMP UnAdvise() override;

    STDMETHODIMP GetFieldDescriptorCount(DWORD* pdwCount) override;

    STDMETHODIMP GetFieldDescriptorAt(
        DWORD dwIndex,
        CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR** ppcpfd) override;

    STDMETHODIMP GetCredentialCount(
        DWORD* pdwCount,
        DWORD* pdwDefault,
        BOOL*  pbAutoLogonWithDefault) override;

    STDMETHODIMP GetCredentialAt(
        DWORD dwIndex,
        ICredentialProviderCredential** ppcpc) override;

private:
    LONG                        m_cRef;
    CREDENTIAL_PROVIDER_USAGE_SCENARIO m_cpus;
    ICredentialProviderEvents*  m_pcpe;
    UINT_PTR                    m_upAdviseContext;
    CMajestyCredential*         m_pCredential;

    // Pipe connection to MajestyGuard Service
    HANDLE                      m_hPipe;

    HRESULT ConnectToService();
    void    DisconnectFromService();
};


// ═══════════════════════════════════════════════════════════════════
// CMajestyCredential
// Implements: ICredentialProviderCredential2
// Represents the single authentication tile on the login screen
// ═══════════════════════════════════════════════════════════════════
class CMajestyCredential : public ICredentialProviderCredential2
{
public:
    CMajestyCredential();
    ~CMajestyCredential();

    HRESULT Initialize(
        CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus,
        ICredentialProviderEvents* pcpe,
        UINT_PTR upAdviseContext,
        HANDLE hPipe);

    // IUnknown
    STDMETHODIMP         QueryInterface(REFIID riid, void** ppv) override;
    STDMETHODIMP_(ULONG) AddRef()  override;
    STDMETHODIMP_(ULONG) Release() override;

    // ICredentialProviderCredential
    STDMETHODIMP Advise(
        ICredentialProviderCredentialEvents* pcpce) override;

    STDMETHODIMP UnAdvise() override;

    STDMETHODIMP SetSelected(BOOL* pbAutoLogon) override;

    STDMETHODIMP SetDeselected() override;

    STDMETHODIMP GetFieldState(
        DWORD dwFieldID,
        CREDENTIAL_PROVIDER_FIELD_STATE* pcpfs,
        CREDENTIAL_PROVIDER_FIELD_INTERACTIVE_STATE* pcpfis) override;

    STDMETHODIMP GetStringValue(DWORD dwFieldID, WCHAR** ppwsz) override;

    STDMETHODIMP GetBitmapValue(DWORD dwFieldID, HBITMAP* phbmp) override;

    STDMETHODIMP GetCheckboxValue(
        DWORD dwFieldID, BOOL* pbChecked, WCHAR** ppwszLabel) override;

    STDMETHODIMP GetComboBoxValueCount(
        DWORD dwFieldID, DWORD* pcItems, DWORD* pdwSelectedItem) override;

    STDMETHODIMP GetComboBoxValueAt(
        DWORD dwFieldID, DWORD dwItem, WCHAR** ppwszItem) override;

    STDMETHODIMP GetSubmitButtonValue(
        DWORD dwFieldID, DWORD* pdwAdjacentTo) override;

    STDMETHODIMP SetStringValue(DWORD dwFieldID, PCWSTR pwz) override;

    STDMETHODIMP SetCheckboxValue(DWORD dwFieldID, BOOL bChecked) override;

    STDMETHODIMP SetComboBoxSelectedValue(DWORD dwFieldID, DWORD dwSelectedItem) override;

    STDMETHODIMP CommandLinkClicked(DWORD dwFieldID) override;

    STDMETHODIMP GetSerialization(
        CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE* pcpgsr,
        CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION*   pcpcs,
        WCHAR** ppwszOptionalStatusText,
        CREDENTIAL_PROVIDER_STATUS_ICON* pcpsiOptionalStatusIcon) override;

    STDMETHODIMP ReportResult(
        NTSTATUS ntsStatus,
        NTSTATUS ntsSubstatus,
        WCHAR** ppwszOptionalStatusText,
        CREDENTIAL_PROVIDER_STATUS_ICON* pcpsiOptionalStatusIcon) override;

    // ICredentialProviderCredential2
    STDMETHODIMP GetUserSid(WCHAR** ppszSid) override;

    // Called by Service (via pipe) when face recognition completes
    void OnAuthDecision(bool granted);

private:
    LONG                                    m_cRef;
    CREDENTIAL_PROVIDER_USAGE_SCENARIO      m_cpus;
    ICredentialProviderCredentialEvents*    m_pcpce;
    HANDLE                                  m_hPipe;
    UINT_PTR                                m_upAdviseContext;

    std::wstring    m_wsStatusText;
    std::atomic<bool> m_authGranted{ false };
    bool            m_fallbackMode{ false };  // User chose "Enter password instead"

    // Background thread: reads auth decisions from pipe
    HANDLE          m_hPipeThread;
    std::atomic<bool> m_stopPipeThread{ false };

    static DWORD WINAPI PipeReaderThread(LPVOID lpParam);

    HRESULT SerializeCredentials(
        CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE* pcpgsr,
        CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION*   pcpcs);

public:
    // Field state table (public — accessed by CMajestyCredentialProvider::GetFieldDescriptorAt)
    static const CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR s_fieldDescriptors[FI_COUNT];

private:
};


// ═══════════════════════════════════════════════════════════════════
// COM DLL Exports
// Required for a COM in-proc server DLL
// ═══════════════════════════════════════════════════════════════════
extern "C"
{
    HRESULT __stdcall DllGetClassObject(REFCLSID clsid, REFIID riid, void** ppv);
    HRESULT __stdcall DllCanUnloadNow();
    HRESULT __stdcall DllRegisterServer();    // Registers CP in HKLM
    HRESULT __stdcall DllUnregisterServer();  // Cleans up registry
    BOOL    WINAPI    DllMain(HINSTANCE hinstDLL, DWORD fdwReason, LPVOID lpReserved);
}
