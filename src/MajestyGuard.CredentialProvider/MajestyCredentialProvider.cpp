// MajestyGuard.CredentialProvider/MajestyCredentialProvider.cpp
// COM DLL implementation for the Windows Credential Provider.

// ── Windows SDK includes — ORDER IS CRITICAL ─────────────────────
// windows.h MUST come first. initguid.h MUST come after windows.h.
// INITGUID must be #defined before initguid.h in exactly ONE .cpp file.
// ─────────────────────────────────────────────────────────────────
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#define STRICT
#define NTDDI_VERSION 0x0A000008
#define _WIN32_WINNT  0x0A00

#include <windows.h>
#include <objbase.h>
#include <ole2.h>
#define INITGUID
#include <initguid.h>
#include <guiddef.h>
#include <wincred.h>
#include <ntsecapi.h>
#include <sddl.h>
#include <securitybaseapi.h>
#include <sspi.h>
#include <credentialprovider.h>
#include <unknwn.h>

#include "MajestyCredentialProvider.h"   // LAST — after all SDK headers

#include <shlwapi.h>
#include <strsafe.h>
#include <olectl.h>

#pragma comment(lib, "credui.lib")
#pragma comment(lib, "shlwapi.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "oleaut32.lib")
#pragma comment(lib, "uuid.lib")
#pragma comment(lib, "ntdll.lib")
#pragma comment(lib, "advapi32.lib")
#pragma comment(lib, "secur32.lib")

// Credential provider field GUIDs (not always exported by credentialprovider.h)
DEFINE_GUID(CPFG_CREDENTIAL_PROVIDER_LABEL,
    0x286BBFF3, 0xBAD4, 0x438F,
    0xB0, 0x07, 0x79, 0xB7, 0x26, 0x7C, 0x3D, 0x48);

DEFINE_GUID(CPFG_CREDENTIAL_PROVIDER_LOGO,
    0x2d837775, 0xf6cd, 0x4e07,
    0xa5, 0x60, 0x2e, 0x27, 0x7c, 0xf8, 0x4c, 0x33);

static const GUID GUID_NULL_DEF = {0, 0, 0, {0, 0, 0, 0, 0, 0, 0, 0}};
#define GUID_NULL GUID_NULL_DEF

// Global DLL reference count
static LONG g_cDllRef = 0;
static HMODULE g_hModule = nullptr;  // Set in DllMain

// Credential Manager target name for stored credentials
static const WCHAR CRED_TARGET[] = L"MajestyGuard_FaceAuth";

// CLSID string form for registry
static const WCHAR CLSID_STRING[] = L"{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}";

// ── Field descriptor table ──────────────────────────────────────
const CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR
CMajestyCredential::s_fieldDescriptors[FI_COUNT] =
{
    {
        FI_STATUS_LABEL,
        CPFT_LARGE_TEXT,
        const_cast<LPWSTR>(L"Verify identity, your majesty"),
        CPFG_CREDENTIAL_PROVIDER_LABEL
    },
    {
        FI_FACE_SCAN_IMAGE,
        CPFT_TILE_IMAGE,
        const_cast<LPWSTR>(L"Face Scan"),
        CPFG_CREDENTIAL_PROVIDER_LOGO
    },
    {
        FI_SUBMIT,
        CPFT_SUBMIT_BUTTON,
        const_cast<LPWSTR>(L"Unlock"),
        GUID_NULL
    },
    {
        FI_FALLBACK_LINK,
        CPFT_COMMAND_LINK,
        const_cast<LPWSTR>(L"Enter password instead"),
        GUID_NULL
    },
};


// ═══════════════════════════════════════════════════════════════════
// CMajestyCredentialProvider
// ═══════════════════════════════════════════════════════════════════

CMajestyCredentialProvider::CMajestyCredentialProvider()
    : m_cRef(1)
    , m_cpus(CPUS_INVALID)
    , m_pcpe(nullptr)
    , m_upAdviseContext(0)
    , m_pCredential(nullptr)
    , m_hPipe(INVALID_HANDLE_VALUE)
{
    InterlockedIncrement(&g_cDllRef);
}

CMajestyCredentialProvider::~CMajestyCredentialProvider()
{
    if (m_pCredential) { m_pCredential->Release(); m_pCredential = nullptr; }
    DisconnectFromService();
    InterlockedDecrement(&g_cDllRef);
}

STDMETHODIMP CMajestyCredentialProvider::QueryInterface(REFIID riid, void** ppv)
{
    if (ppv == nullptr) return E_POINTER;

    if (riid == IID_IUnknown || riid == IID_ICredentialProvider)
    {
        *ppv = static_cast<ICredentialProvider*>(this);
        AddRef();
        return S_OK;
    }

    *ppv = nullptr;
    return E_NOINTERFACE;
}

STDMETHODIMP_(ULONG) CMajestyCredentialProvider::AddRef()  { return InterlockedIncrement(&m_cRef); }
STDMETHODIMP_(ULONG) CMajestyCredentialProvider::Release()
{
    auto ref = InterlockedDecrement(&m_cRef);
    if (ref == 0) delete this;
    return ref;
}

STDMETHODIMP CMajestyCredentialProvider::SetUsageScenario(
    CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus, DWORD /*dwFlags*/)
{
    switch (cpus)
    {
    case CPUS_LOGON:
    case CPUS_UNLOCK_WORKSTATION:
        m_cpus = cpus;
        return ConnectToService();

    default:
        return E_NOTIMPL;
    }
}

STDMETHODIMP CMajestyCredentialProvider::SetSerialization(
    const CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* /*pcpcs*/)
{
    return E_NOTIMPL;
}

STDMETHODIMP CMajestyCredentialProvider::Advise(
    ICredentialProviderEvents* pcpe, UINT_PTR upAdviseContext)
{
    if (m_pcpe) { m_pcpe->Release(); }
    m_pcpe = pcpe;
    if (m_pcpe) { m_pcpe->AddRef(); }
    m_upAdviseContext = upAdviseContext;
    return S_OK;
}

STDMETHODIMP CMajestyCredentialProvider::UnAdvise()
{
    if (m_pcpe) { m_pcpe->Release(); m_pcpe = nullptr; }
    return S_OK;
}

STDMETHODIMP CMajestyCredentialProvider::GetFieldDescriptorCount(DWORD* pdwCount)
{
    *pdwCount = FI_COUNT;
    return S_OK;
}

STDMETHODIMP CMajestyCredentialProvider::GetFieldDescriptorAt(
    DWORD dwIndex, CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR** ppcpfd)
{
    if (dwIndex >= FI_COUNT || ppcpfd == nullptr) return E_INVALIDARG;

    *ppcpfd = static_cast<CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR*>(
        CoTaskMemAlloc(sizeof(CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR)));
    if (!*ppcpfd) return E_OUTOFMEMORY;

    **ppcpfd = CMajestyCredential::s_fieldDescriptors[dwIndex];

    if ((*ppcpfd)->pszLabel)
    {
        auto len = wcslen((*ppcpfd)->pszLabel) + 1;
        (*ppcpfd)->pszLabel = static_cast<LPWSTR>(CoTaskMemAlloc(len * sizeof(wchar_t)));
        if (!(*ppcpfd)->pszLabel) { CoTaskMemFree(*ppcpfd); return E_OUTOFMEMORY; }
        StringCchCopyW((*ppcpfd)->pszLabel, len,
            CMajestyCredential::s_fieldDescriptors[dwIndex].pszLabel);
    }

    return S_OK;
}

STDMETHODIMP CMajestyCredentialProvider::GetCredentialCount(
    DWORD* pdwCount, DWORD* pdwDefault, BOOL* pbAutoLogonWithDefault)
{
    *pdwCount  = 1;
    *pdwDefault = 0;
    *pbAutoLogonWithDefault = FALSE;
    return S_OK;
}

STDMETHODIMP CMajestyCredentialProvider::GetCredentialAt(
    DWORD dwIndex, ICredentialProviderCredential** ppcpc)
{
    if (dwIndex != 0 || ppcpc == nullptr) return E_INVALIDARG;

    if (!m_pCredential)
    {
        m_pCredential = new (std::nothrow) CMajestyCredential();
        if (!m_pCredential) return E_OUTOFMEMORY;

        auto hr = m_pCredential->Initialize(
            m_cpus, m_pcpe, m_upAdviseContext, m_hPipe);
        if (FAILED(hr)) { m_pCredential->Release(); m_pCredential = nullptr; return hr; }
    }

    *ppcpc = m_pCredential;
    (*ppcpc)->AddRef();
    return S_OK;
}

HRESULT CMajestyCredentialProvider::ConnectToService()
{
    WCHAR pipeName[] = L"\\\\.\\pipe\\MajestyGuard_CredProv";
    const DWORD TIMEOUT_MS = 5000;

    if (!WaitNamedPipeW(pipeName, TIMEOUT_MS))
        return S_OK;

    m_hPipe = CreateFileW(
        pipeName,
        GENERIC_READ | GENERIC_WRITE,
        0, nullptr,
        OPEN_EXISTING, 0, nullptr);

    if (m_hPipe == INVALID_HANDLE_VALUE) return S_OK;

    const char* hello = "{\"cmd\":\"CredProvConnected\"}\n";
    DWORD written;
    WriteFile(m_hPipe, hello, static_cast<DWORD>(strlen(hello)), &written, nullptr);

    return S_OK;
}

void CMajestyCredentialProvider::DisconnectFromService()
{
    if (m_hPipe != INVALID_HANDLE_VALUE)
    {
        CloseHandle(m_hPipe);
        m_hPipe = INVALID_HANDLE_VALUE;
    }
}


// ═══════════════════════════════════════════════════════════════════
// CMajestyCredential
// ═══════════════════════════════════════════════════════════════════

CMajestyCredential::CMajestyCredential()
    : m_cRef(1)
    , m_cpus(CPUS_INVALID)
    , m_pcpce(nullptr)
    , m_hPipe(INVALID_HANDLE_VALUE)
    , m_upAdviseContext(0)
    , m_wsStatusText(L"Verify identity, your majesty")
    , m_hPipeThread(nullptr)
{
}

CMajestyCredential::~CMajestyCredential()
{
    m_stopPipeThread = true;
    if (m_hPipeThread)
    {
        WaitForSingleObject(m_hPipeThread, 2000);
        CloseHandle(m_hPipeThread);
    }
    if (m_pcpce) m_pcpce->Release();
}

HRESULT CMajestyCredential::Initialize(
    CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus,
    ICredentialProviderEvents* pcpe,
    UINT_PTR upAdviseContext,
    HANDLE hPipe)
{
    m_cpus = cpus;
    m_upAdviseContext = upAdviseContext;
    m_hPipe = hPipe;
    return S_OK;
}

STDMETHODIMP CMajestyCredential::QueryInterface(REFIID riid, void** ppv)
{
    if (ppv == nullptr) return E_POINTER;

    if (riid == IID_IUnknown ||
        riid == IID_ICredentialProviderCredential ||
        riid == __uuidof(ICredentialProviderCredential2))
    {
        *ppv = static_cast<ICredentialProviderCredential2*>(this);
        AddRef();
        return S_OK;
    }

    *ppv = nullptr;
    return E_NOINTERFACE;
}

STDMETHODIMP_(ULONG) CMajestyCredential::AddRef()  { return InterlockedIncrement(&m_cRef); }
STDMETHODIMP_(ULONG) CMajestyCredential::Release()
{
    auto ref = InterlockedDecrement(&m_cRef);
    if (ref == 0) delete this;
    return ref;
}

STDMETHODIMP CMajestyCredential::Advise(ICredentialProviderCredentialEvents* pcpce)
{
    if (m_pcpce) m_pcpce->Release();
    m_pcpce = pcpce;
    if (m_pcpce) m_pcpce->AddRef();
    return S_OK;
}

STDMETHODIMP CMajestyCredential::UnAdvise()
{
    if (m_pcpce) { m_pcpce->Release(); m_pcpce = nullptr; }
    return S_OK;
}

STDMETHODIMP CMajestyCredential::SetSelected(BOOL* pbAutoLogon)
{
    *pbAutoLogon = FALSE;

    // Start pipe reader thread to listen for auth decisions
    if (m_hPipe != INVALID_HANDLE_VALUE && !m_hPipeThread)
    {
        m_stopPipeThread = false;
        m_hPipeThread = CreateThread(
            nullptr, 0, PipeReaderThread, this, 0, nullptr);
    }

    return S_OK;
}

STDMETHODIMP CMajestyCredential::SetDeselected()
{
    m_stopPipeThread = true;
    return S_OK;
}

STDMETHODIMP CMajestyCredential::GetFieldState(
    DWORD dwFieldID,
    CREDENTIAL_PROVIDER_FIELD_STATE* pcpfs,
    CREDENTIAL_PROVIDER_FIELD_INTERACTIVE_STATE* pcpfis)
{
    if (dwFieldID >= FI_COUNT) return E_INVALIDARG;

    switch (dwFieldID)
    {
    case FI_STATUS_LABEL:
        *pcpfs  = CPFS_DISPLAY_IN_BOTH;
        *pcpfis = CPFIS_NONE;
        break;
    case FI_FACE_SCAN_IMAGE:
        *pcpfs  = CPFS_DISPLAY_IN_BOTH;
        *pcpfis = CPFIS_NONE;
        break;
    case FI_SUBMIT:
        *pcpfs  = CPFS_HIDDEN;
        *pcpfis = CPFIS_NONE;
        break;
    case FI_FALLBACK_LINK:
        *pcpfs  = CPFS_DISPLAY_IN_BOTH;
        *pcpfis = CPFIS_NONE;
        break;
    default:
        return E_INVALIDARG;
    }

    return S_OK;
}

STDMETHODIMP CMajestyCredential::GetStringValue(DWORD dwFieldID, WCHAR** ppwsz)
{
    if (dwFieldID == FI_STATUS_LABEL)
    {
        return SHStrDupW(m_wsStatusText.c_str(), ppwsz);
    }
    if (dwFieldID == FI_FALLBACK_LINK)
    {
        return SHStrDupW(L"Enter password instead", ppwsz);
    }
    return SHStrDupW(L"", ppwsz);
}

STDMETHODIMP CMajestyCredential::GetBitmapValue(DWORD /*dwFieldID*/, HBITMAP* phbmp)
{
    *phbmp = nullptr;
    return E_NOTIMPL;
}

STDMETHODIMP CMajestyCredential::GetCheckboxValue(DWORD, BOOL*, WCHAR**)        { return E_NOTIMPL; }
STDMETHODIMP CMajestyCredential::GetComboBoxValueCount(DWORD, DWORD*, DWORD*)   { return E_NOTIMPL; }
STDMETHODIMP CMajestyCredential::GetComboBoxValueAt(DWORD, DWORD, WCHAR**)      { return E_NOTIMPL; }
STDMETHODIMP CMajestyCredential::SetStringValue(DWORD, PCWSTR)                  { return E_NOTIMPL; }
STDMETHODIMP CMajestyCredential::SetCheckboxValue(DWORD, BOOL)                  { return E_NOTIMPL; }
STDMETHODIMP CMajestyCredential::SetComboBoxSelectedValue(DWORD, DWORD)          { return E_NOTIMPL; }

STDMETHODIMP CMajestyCredential::GetSubmitButtonValue(DWORD dwFieldID, DWORD* pdwAdjacentTo)
{
    if (dwFieldID == FI_SUBMIT)
    {
        *pdwAdjacentTo = FI_STATUS_LABEL;
        return S_OK;
    }
    return E_INVALIDARG;
}

STDMETHODIMP CMajestyCredential::CommandLinkClicked(DWORD dwFieldID)
{
    if (dwFieldID == FI_FALLBACK_LINK)
    {
        m_fallbackMode = true;
        m_wsStatusText = L"Enter your password";

        if (m_pcpce)
            m_pcpce->SetFieldString(this, FI_STATUS_LABEL, L"Enter your password");

        return S_OK;
    }
    return E_INVALIDARG;
}

// FIX-001 (B-024): DO NOT store or read passwords from Credential Manager.
// The CP is a GATEKEEPER only — it controls whether the Windows
// password prompt appears, not what the credential is.
// Face recognition success → show standard password field with hint.
// V2: Replace with TPM-backed Virtual Smart Card.
STDMETHODIMP CMajestyCredential::GetSerialization(
    CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE* pcpgsr,
    CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION*   pcpcs,
    WCHAR** ppwszOptionalStatusText,
    CREDENTIAL_PROVIDER_STATUS_ICON* pcpsiOptionalStatusIcon)
{
    *ppwszOptionalStatusText = nullptr;
    *pcpsiOptionalStatusIcon = CPSI_NONE;

    if (m_fallbackMode)
    {
        *pcpgsr = CPGSR_NO_CREDENTIAL_NOT_FINISHED;
        return S_OK;
    }

    if (!m_authGranted.load())
    {
        *pcpgsr = CPGSR_NO_CREDENTIAL_NOT_FINISHED;
        return S_OK;
    }

    // Face recognized. Show status hint and let user enter password normally.
    // CPGSR_NO_CREDENTIAL_FINISHED signals LogonUI that we are done but
    // have no serialized credential — Windows shows the next available
    // credential provider (the password field).
    SHStrDupW(L"Face recognized \u2014 enter your password to confirm", ppwszOptionalStatusText);
    *pcpsiOptionalStatusIcon = CPSI_SUCCESS;
    *pcpgsr = CPGSR_NO_CREDENTIAL_FINISHED;
    return S_OK;
}

HRESULT CMajestyCredential::SerializeCredentials(
    CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE* pcpgsr,
    CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION*   pcpcs)
{
    PCREDENTIALW pCred = nullptr;
    if (!CredReadW(CRED_TARGET, CRED_TYPE_GENERIC, 0, &pCred))
    {
        *pcpgsr = CPGSR_NO_CREDENTIAL_NOT_FINISHED;
        return S_OK;
    }

    DWORD passwordLen = pCred->CredentialBlobSize / sizeof(WCHAR);
    std::wstring password((WCHAR*)pCred->CredentialBlob, passwordLen);
    std::wstring username(pCred->UserName ? pCred->UserName : L"");

    CredFree(pCred);

    WCHAR domain[MAX_COMPUTERNAME_LENGTH + 1] = {};
    DWORD domainSize = _countof(domain);
    GetComputerNameW(domain, &domainSize);

    USHORT cbDomain   = static_cast<USHORT>(wcslen(domain) * sizeof(WCHAR));
    USHORT cbUsername  = static_cast<USHORT>(username.size() * sizeof(WCHAR));
    USHORT cbPassword  = static_cast<USHORT>(password.size() * sizeof(WCHAR));

    // Pack strings contiguously after the struct header.
    // Buffer fields are byte offsets from start of serialized blob.
    DWORD cbHeader = sizeof(KERB_INTERACTIVE_UNLOCK_LOGON);
    DWORD cbSerialization = cbHeader + cbDomain + cbUsername + cbPassword;

    pcpcs->rgbSerialization = static_cast<BYTE*>(CoTaskMemAlloc(cbSerialization));
    if (!pcpcs->rgbSerialization) return E_OUTOFMEMORY;

    ZeroMemory(pcpcs->rgbSerialization, cbSerialization);

    auto* pKiul = reinterpret_cast<KERB_INTERACTIVE_UNLOCK_LOGON*>(pcpcs->rgbSerialization);
    KERB_INTERACTIVE_LOGON& kil = pKiul->Logon;
    kil.MessageType = KerbInteractiveLogon;

    // String data packed right after the struct
    BYTE* pStrings = pcpcs->rgbSerialization + cbHeader;

    // Domain — offset-relative buffer pointer
    kil.LogonDomainName.Length        = cbDomain;
    kil.LogonDomainName.MaximumLength = cbDomain;
    kil.LogonDomainName.Buffer        = reinterpret_cast<PWSTR>(pStrings - pcpcs->rgbSerialization);
    memcpy(pStrings, domain, cbDomain);
    pStrings += cbDomain;

    // Username
    kil.UserName.Length        = cbUsername;
    kil.UserName.MaximumLength = cbUsername;
    kil.UserName.Buffer        = reinterpret_cast<PWSTR>(pStrings - pcpcs->rgbSerialization);
    memcpy(pStrings, username.c_str(), cbUsername);
    pStrings += cbUsername;

    // Password
    kil.Password.Length        = cbPassword;
    kil.Password.MaximumLength = cbPassword;
    kil.Password.Buffer        = reinterpret_cast<PWSTR>(pStrings - pcpcs->rgbSerialization);
    memcpy(pStrings, password.c_str(), cbPassword);

    pcpcs->cbSerialization = cbSerialization;

    // Kerberos auth package
    ULONG authPackage = 0;
    HANDLE hLsa = nullptr;
    NTSTATUS status = LsaConnectUntrusted(&hLsa);
    if (SUCCEEDED(HRESULT_FROM_NT(status)))
    {
        LSA_STRING authPkgName = {};
        authPkgName.Buffer = const_cast<PCHAR>(MICROSOFT_KERBEROS_NAME_A);
        authPkgName.Length = static_cast<USHORT>(strlen(MICROSOFT_KERBEROS_NAME_A));
        authPkgName.MaximumLength = authPkgName.Length + 1;

        LsaLookupAuthenticationPackage(hLsa, &authPkgName, &authPackage);
        LsaDeregisterLogonProcess(hLsa);
    }
    pcpcs->ulAuthenticationPackage = authPackage;
    pcpcs->clsidCredentialProvider = CLSID_MajestyCredentialProvider;

    *pcpgsr = CPGSR_RETURN_CREDENTIAL_FINISHED;

    SecureZeroMemory(const_cast<PWSTR>(password.c_str()), password.size() * sizeof(WCHAR));

    return S_OK;
}

STDMETHODIMP CMajestyCredential::ReportResult(
    NTSTATUS /*ntsStatus*/, NTSTATUS /*ntsSubstatus*/,
    WCHAR** ppwszOptionalStatusText,
    CREDENTIAL_PROVIDER_STATUS_ICON* pcpsiOptionalStatusIcon)
{
    *ppwszOptionalStatusText = nullptr;
    *pcpsiOptionalStatusIcon = CPSI_NONE;
    return S_OK;
}

// FIX-018 (B-035): GetUserSid must return the ENROLLED user's SID, not SYSTEM.
// OpenProcessToken(GetCurrentProcess()) returns SYSTEM SID in LogonUI context.
// Read enrolled SID from HKLM\SOFTWARE\MajestyGuard\EnrolledUserSid instead.
STDMETHODIMP CMajestyCredential::GetUserSid(WCHAR** ppszSid)
{
    *ppszSid = nullptr;

    HKEY hKey = nullptr;
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, L"SOFTWARE\\MajestyGuard",
        0, KEY_READ, &hKey) != ERROR_SUCCESS)
        return E_FAIL;

    WCHAR sid[256] = {};
    DWORD size = sizeof(sid);
    DWORD type = REG_SZ;
    HRESULT hr = E_FAIL;

    if (RegQueryValueExW(hKey, L"EnrolledUserSid", nullptr, &type,
        reinterpret_cast<LPBYTE>(sid), &size) == ERROR_SUCCESS &&
        type == REG_SZ && sid[0] != L'\0')
    {
        hr = SHStrDupW(sid, ppszSid);
    }

    RegCloseKey(hKey);
    return hr;
}

void CMajestyCredential::OnAuthDecision(bool granted)
{
    m_authGranted = granted;
    if (granted)
    {
        m_wsStatusText = L"Welcome back, your majesty";
        if (m_pcpce)
            m_pcpce->SetFieldString(this, FI_STATUS_LABEL, m_wsStatusText.c_str());

        // Signal LogonUI to call GetSerialization — triggers auto-logon
        if (m_pcpce)
            m_pcpce->SetFieldSubmitButton(this, FI_SUBMIT, FI_STATUS_LABEL);
    }
}

DWORD WINAPI CMajestyCredential::PipeReaderThread(LPVOID lpParam)
{
    auto self = static_cast<CMajestyCredential*>(lpParam);
    if (self->m_hPipe == INVALID_HANDLE_VALUE) return 1;

    char buffer[1024] = {};
    std::string accumulated;

    while (!self->m_stopPipeThread.load())
    {
        DWORD bytesRead = 0;
        BOOL ok = ReadFile(self->m_hPipe, buffer, sizeof(buffer) - 1, &bytesRead, nullptr);

        if (!ok || bytesRead == 0)
        {
            DWORD err = GetLastError();
            if (err == ERROR_BROKEN_PIPE || err == ERROR_NO_DATA)
                break;
            Sleep(100);
            continue;
        }

        buffer[bytesRead] = '\0';
        accumulated += buffer;

        // Parse newline-delimited JSON
        size_t pos;
        while ((pos = accumulated.find('\n')) != std::string::npos)
        {
            std::string line = accumulated.substr(0, pos);
            accumulated = accumulated.substr(pos + 1);

            // Skip lines that don't look like JSON objects
            size_t first = line.find_first_not_of(" \t\r");
            size_t last  = line.find_last_not_of(" \t\r");
            if (first == std::string::npos || line[first] != '{' || line[last] != '}')
                continue;

            // FIX-002 (B-015): Safer JSON field check.
            // Verify MessageType field is specifically AuthDecision,
            // then check Granted field independently.
            if (line.find("\"MessageType\":\"AuthDecision\"") != std::string::npos ||
                line.find("\"MessageType\": \"AuthDecision\"") != std::string::npos)
            {
                // Verify Granted field is a boolean true — must follow : with no extra text
                bool granted = (line.find("\"Granted\":true") != std::string::npos ||
                                line.find("\"Granted\": true") != std::string::npos) &&
                               (line.find("\"Granted\":false") == std::string::npos &&
                                line.find("\"Granted\": false") == std::string::npos);
                self->OnAuthDecision(granted);
            }
        }
    }

    return 0;
}


// ═══════════════════════════════════════════════════════════════════
// COM DLL EXPORTS
// ═══════════════════════════════════════════════════════════════════

class CMajestyCredentialProviderFactory : public IClassFactory
{
public:
    STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override
    {
        if (riid == IID_IUnknown || riid == IID_IClassFactory)
        {
            *ppv = static_cast<IClassFactory*>(this);
            AddRef();
            return S_OK;
        }
        *ppv = nullptr;
        return E_NOINTERFACE;
    }
    STDMETHODIMP_(ULONG) AddRef()  override { return 2; }
    STDMETHODIMP_(ULONG) Release() override { return 1; }

    STDMETHODIMP CreateInstance(IUnknown* pOuter, REFIID riid, void** ppv) override
    {
        if (pOuter) return CLASS_E_NOAGGREGATION;
        auto pCP = new (std::nothrow) CMajestyCredentialProvider();
        if (!pCP) return E_OUTOFMEMORY;
        auto hr = pCP->QueryInterface(riid, ppv);
        pCP->Release();
        return hr;
    }

    STDMETHODIMP LockServer(BOOL fLock) override
    {
        if (fLock) InterlockedIncrement(&g_cDllRef);
        else       InterlockedDecrement(&g_cDllRef);
        return S_OK;
    }
};

static CMajestyCredentialProviderFactory s_factory;

HRESULT __stdcall DllGetClassObject(REFCLSID clsid, REFIID riid, void** ppv)
{
    if (clsid == CLSID_MajestyCredentialProvider)
        return s_factory.QueryInterface(riid, ppv);
    return CLASS_E_CLASSNOTAVAILABLE;
}

HRESULT __stdcall DllCanUnloadNow()
{
    return g_cDllRef > 0 ? S_FALSE : S_OK;
}

HRESULT __stdcall DllRegisterServer()
{
    // Get path to this DLL
    WCHAR dllPath[MAX_PATH] = {};
    if (!GetModuleFileNameW(g_hModule, dllPath, MAX_PATH))
        return SELFREG_E_CLASS;

    HKEY hKey = nullptr;
    LSTATUS lr;

    // 1. COM Server registration
    // HKLM\SOFTWARE\Classes\CLSID\{CLSID}\InprocServer32
    WCHAR comKey[256];
    StringCchPrintfW(comKey, _countof(comKey),
        L"SOFTWARE\\Classes\\CLSID\\%s\\InprocServer32", CLSID_STRING);

    lr = RegCreateKeyExW(HKEY_LOCAL_MACHINE, comKey, 0, nullptr,
        REG_OPTION_NON_VOLATILE, KEY_WRITE, nullptr, &hKey, nullptr);
    if (lr != ERROR_SUCCESS) return SELFREG_E_CLASS;

    lr = RegSetValueExW(hKey, nullptr, 0, REG_SZ,
        reinterpret_cast<const BYTE*>(dllPath),
        static_cast<DWORD>((wcslen(dllPath) + 1) * sizeof(WCHAR)));
    if (lr != ERROR_SUCCESS) { RegCloseKey(hKey); return SELFREG_E_CLASS; }

    const WCHAR threadModel[] = L"Apartment";
    lr = RegSetValueExW(hKey, L"ThreadingModel", 0, REG_SZ,
        reinterpret_cast<const BYTE*>(threadModel),
        static_cast<DWORD>(sizeof(threadModel)));
    RegCloseKey(hKey);
    if (lr != ERROR_SUCCESS) return SELFREG_E_CLASS;

    // 2. Credential Provider registration
    WCHAR cpKey[256];
    StringCchPrintfW(cpKey, _countof(cpKey),
        L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Authentication\\Credential Providers\\%s",
        CLSID_STRING);

    lr = RegCreateKeyExW(HKEY_LOCAL_MACHINE, cpKey, 0, nullptr,
        REG_OPTION_NON_VOLATILE, KEY_WRITE, nullptr, &hKey, nullptr);
    if (lr != ERROR_SUCCESS) return SELFREG_E_CLASS;

    const WCHAR cpName[] = L"MajestyGuard";
    lr = RegSetValueExW(hKey, nullptr, 0, REG_SZ,
        reinterpret_cast<const BYTE*>(cpName),
        static_cast<DWORD>(sizeof(cpName)));
    RegCloseKey(hKey);

    return (lr == ERROR_SUCCESS) ? S_OK : SELFREG_E_CLASS;
}

HRESULT __stdcall DllUnregisterServer()
{
    // Remove COM server key
    WCHAR comKey[256];
    StringCchPrintfW(comKey, _countof(comKey),
        L"SOFTWARE\\Classes\\CLSID\\%s", CLSID_STRING);
    RegDeleteTreeW(HKEY_LOCAL_MACHINE, comKey);

    // Remove Credential Provider key
    WCHAR cpKey[256];
    StringCchPrintfW(cpKey, _countof(cpKey),
        L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Authentication\\Credential Providers\\%s",
        CLSID_STRING);
    RegDeleteKeyW(HKEY_LOCAL_MACHINE, cpKey);

    return S_OK;
}

// DLL entry point — captures module handle for DllRegisterServer
BOOL WINAPI DllMain(HMODULE hModule, DWORD dwReason, LPVOID)
{
    if (dwReason == DLL_PROCESS_ATTACH)
    {
        g_hModule = hModule;
        DisableThreadLibraryCalls(hModule);
    }
    return TRUE;
}
