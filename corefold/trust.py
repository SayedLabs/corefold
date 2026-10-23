"""Who made a program, and where a file came from, read only from this PC.

- signature(): the file's digital signature, checked the way Windows checks it before it runs a program
  (WinVerifyTrust), with the file's own signature or Windows' catalogs (most of Windows' own files are
  signed there). Nothing is fetched from the internet: the check uses only what Windows already has
  (no revocation lookups, cached certificates only), so a signature that would need the internet to be
  checked fully is "couldn't check", never "bad".
- downloaded_from(): the note Windows' browsers and email programs put on a file they downloaded
  ("Mark of the Web"): which site it came from."""
from __future__ import annotations

import ctypes
import logging
import os
import sys
from dataclasses import dataclass
from urllib.parse import urlsplit

log = logging.getLogger("corefold")

# what the signature says
SIGNED = "signed"         # signed, and Windows trusts who signed it
UNSIGNED = "unsigned"     # nobody signed it
BROKEN = "broken"         # signed, but the file was changed after it was signed
UNTRUSTED = "untrusted"   # signed by someone Windows doesn't trust, or has been told to distrust
EXPIRED = "expired"       # signed with a certificate that ran out before it was used (and no time stamp)
UNKNOWN = "unknown"       # couldn't tell (not a program, can't be read, or needs the internet to check)

# the kinds of files that carry a signature (and run)
SIGNABLE = {".exe", ".dll", ".sys", ".scr", ".com", ".cpl", ".ocx", ".msi", ".msix", ".appx", ".ps1", ".vbs",
            ".js", ".cab", ".cat", ".efi"}

TRUST_E_NOSIGNATURE = 0x800B0100
TRUST_E_SUBJECT_FORM_UNKNOWN = 0x800B0003
TRUST_E_PROVIDER_UNKNOWN = 0x800B0001
TRUST_E_BAD_DIGEST = 0x80096010
CERT_E_EXPIRED = 0x800B0101
CERT_E_UNTRUSTEDROOT = 0x800B0109
CERT_E_REVOKED = 0x800B010C
TRUST_E_EXPLICIT_DISTRUST = 0x800B0111
CERT_E_UNTRUSTEDTESTROOT = 0x800B010D
TRUST_E_CERT_SIGNATURE = 0x80096004
CRYPT_E_FILE_ERROR = 0x80092003
NOT_SIGNED = {TRUST_E_NOSIGNATURE, TRUST_E_SUBJECT_FORM_UNKNOWN, TRUST_E_PROVIDER_UNKNOWN}
TAMPERED = {TRUST_E_BAD_DIGEST, TRUST_E_CERT_SIGNATURE}
DISTRUSTED = {CERT_E_UNTRUSTEDROOT, CERT_E_REVOKED, TRUST_E_EXPLICIT_DISTRUST, CERT_E_UNTRUSTEDTESTROOT}


@dataclass
class Signature:
    state: str            # SIGNED, UNSIGNED, BROKEN, UNTRUSTED, EXPIRED or UNKNOWN
    signer: str = ""      # "Microsoft Corporation" (when signed)
    catalog: bool = False  # signed in one of Windows' catalogs, not in the file itself
    code: int = 0          # what Windows answered (for the log)

    @property
    def ok(self) -> bool:
        return self.state == SIGNED


# ----------------------------------------------------------------------------- Windows' structures

class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]


def _guid(a, b, c, d: bytes) -> GUID:
    g = GUID(a, b, c)
    g.Data4[:] = list(d)
    return g


# WINTRUST_ACTION_GENERIC_VERIFY_V2: "is this file signed by someone Windows trusts"
VERIFY_V2 = _guid(0x00AAC56B, 0xCD44, 0x11D0, bytes([0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE]))


class WINTRUST_FILE_INFO(ctypes.Structure):
    _fields_ = [("cbStruct", ctypes.c_uint32), ("pcwszFilePath", ctypes.c_wchar_p), ("hFile", ctypes.c_void_p),
                ("pgKnownSubject", ctypes.c_void_p)]


class WINTRUST_CATALOG_INFO(ctypes.Structure):
    _fields_ = [("cbStruct", ctypes.c_uint32), ("dwCatalogVersion", ctypes.c_uint32),
                ("pcwszCatalogFilePath", ctypes.c_wchar_p), ("pcwszMemberTag", ctypes.c_wchar_p),
                ("pcwszMemberFilePath", ctypes.c_wchar_p), ("hMemberFile", ctypes.c_void_p),
                ("pbCalculatedFileHash", ctypes.c_void_p), ("cbCalculatedFileHash", ctypes.c_uint32),
                ("pcCatalogContext", ctypes.c_void_p), ("hCatAdmin", ctypes.c_void_p)]


class WINTRUST_DATA(ctypes.Structure):
    _fields_ = [("cbStruct", ctypes.c_uint32), ("pPolicyCallbackData", ctypes.c_void_p),
                ("pSIPClientData", ctypes.c_void_p), ("dwUIChoice", ctypes.c_uint32),
                ("fdwRevocationChecks", ctypes.c_uint32), ("dwUnionChoice", ctypes.c_uint32),
                ("pUnion", ctypes.c_void_p), ("dwStateAction", ctypes.c_uint32), ("hWVTStateData", ctypes.c_void_p),
                ("pwszURLReference", ctypes.c_wchar_p), ("dwProvFlags", ctypes.c_uint32),
                ("dwUIContext", ctypes.c_uint32), ("pSignatureSettings", ctypes.c_void_p)]


class CATALOG_INFO(ctypes.Structure):
    _fields_ = [("cbStruct", ctypes.c_uint32), ("wszCatalogFile", ctypes.c_wchar * 260)]


class FILETIME(ctypes.Structure):
    _fields_ = [("lo", ctypes.c_uint32), ("hi", ctypes.c_uint32)]


class CRYPT_PROVIDER_CERT(ctypes.Structure):
    _fields_ = [("cbStruct", ctypes.c_uint32), ("pCert", ctypes.c_void_p)]  # (only the start is read)


class CRYPT_PROVIDER_SGNR(ctypes.Structure):
    _fields_ = [("cbStruct", ctypes.c_uint32), ("sftVerifyAsOf", FILETIME), ("csCertChain", ctypes.c_uint32),
                ("pasCertChain", ctypes.POINTER(CRYPT_PROVIDER_CERT))]  # (only the start is read)


WTD_UI_NONE, WTD_REVOKE_NONE = 2, 0
WTD_CHOICE_FILE, WTD_CHOICE_CATALOG = 1, 2
WTD_STATEACTION_VERIFY, WTD_STATEACTION_CLOSE = 1, 2
WTD_REVOCATION_CHECK_NONE = 0x10
WTD_CACHE_ONLY_URL_RETRIEVAL = 0x1000   # never goes to the internet for a certificate or a list
CERT_NAME_SIMPLE_DISPLAY_TYPE = 4
INVALID_HANDLE = ctypes.c_void_p(-1).value


_dlls: dict = {}


def _api():
    """wintrust, crypt32 and kernel32 with their argument types set once."""
    if _dlls:
        return _dlls
    wt = ctypes.WinDLL("wintrust", use_last_error=True)
    c32 = ctypes.WinDLL("crypt32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    wt.WinVerifyTrust.argtypes = [ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(WINTRUST_DATA)]
    wt.WinVerifyTrust.restype = ctypes.c_long
    wt.WTHelperProvDataFromStateData.argtypes = [ctypes.c_void_p]
    wt.WTHelperProvDataFromStateData.restype = ctypes.c_void_p
    wt.WTHelperGetProvSignerFromChain.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    wt.WTHelperGetProvSignerFromChain.restype = ctypes.POINTER(CRYPT_PROVIDER_SGNR)
    wt.CryptCATAdminAcquireContext2.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_wchar_p,
                                                ctypes.c_void_p, ctypes.c_uint32]
    wt.CryptCATAdminAcquireContext2.restype = ctypes.c_int
    wt.CryptCATAdminCalcHashFromFileHandle2.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                                        ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p,
                                                        ctypes.c_uint32]
    wt.CryptCATAdminCalcHashFromFileHandle2.restype = ctypes.c_int
    wt.CryptCATAdminEnumCatalogFromHash.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                                                    ctypes.c_uint32, ctypes.c_void_p]
    wt.CryptCATAdminEnumCatalogFromHash.restype = ctypes.c_void_p
    wt.CryptCATCatalogInfoFromContext.argtypes = [ctypes.c_void_p, ctypes.POINTER(CATALOG_INFO), ctypes.c_uint32]
    wt.CryptCATCatalogInfoFromContext.restype = ctypes.c_int
    wt.CryptCATAdminReleaseCatalogContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
    wt.CryptCATAdminReleaseContext.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    c32.CertGetNameStringW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                       ctypes.c_wchar_p, ctypes.c_uint32]
    c32.CertGetNameStringW.restype = ctypes.c_uint32
    k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    _dlls.update(wt=wt, c32=c32, k32=k32)
    return _dlls


def _verify(choice: int, info, path: str) -> tuple[int, str]:
    """Asks Windows about one file (or one catalog entry): (its answer, who signed it)."""
    api = _api()
    data = WINTRUST_DATA()
    data.cbStruct = ctypes.sizeof(data)
    data.dwUIChoice = WTD_UI_NONE
    data.fdwRevocationChecks = WTD_REVOKE_NONE
    data.dwUnionChoice = choice
    data.pUnion = ctypes.cast(ctypes.pointer(info), ctypes.c_void_p)
    data.dwStateAction = WTD_STATEACTION_VERIFY
    data.dwProvFlags = WTD_REVOCATION_CHECK_NONE | WTD_CACHE_ONLY_URL_RETRIEVAL
    action = GUID.from_buffer_copy(VERIFY_V2)
    code = api["wt"].WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(data)) & 0xFFFFFFFF
    signer = ""
    try:
        if code not in NOT_SIGNED and data.hWVTStateData:
            signer = _signer(data.hWVTStateData)
    except Exception:
        log.debug("signer of %s", path, exc_info=True)
    finally:
        data.dwStateAction = WTD_STATEACTION_CLOSE
        api["wt"].WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(data))
    return code, signer


def _signer(state) -> str:
    api = _api()
    prov = api["wt"].WTHelperProvDataFromStateData(state)
    if not prov:
        return ""
    sgnr = api["wt"].WTHelperGetProvSignerFromChain(prov, 0, False, 0)
    if not sgnr or not sgnr.contents.csCertChain or not sgnr.contents.pasCertChain:
        return ""
    cert = sgnr.contents.pasCertChain[0].pCert
    if not cert:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    api["c32"].CertGetNameStringW(cert, CERT_NAME_SIMPLE_DISPLAY_TYPE, 0, None, buf, 256)
    return buf.value.strip()


def _catalog(path: str) -> tuple[int, str] | None:
    """The file's entry in one of Windows' catalogs: (Windows' answer, signer), or None if it has none."""
    api = _api()
    wt, k32 = api["wt"], api["k32"]
    handle = k32.CreateFileW(path, 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0, None)  # read, share all, existing
    if not handle or handle == INVALID_HANDLE:
        return None
    try:
        for algorithm in ("SHA256", "SHA1"):
            admin = ctypes.c_void_p()
            if not wt.CryptCATAdminAcquireContext2(ctypes.byref(admin), None, algorithm, None, 0):
                continue
            try:
                size = ctypes.c_uint32(0)
                wt.CryptCATAdminCalcHashFromFileHandle2(admin, handle, ctypes.byref(size), None, 0)
                if not size.value or size.value > 256:
                    continue
                digest = (ctypes.c_ubyte * size.value)()
                if not wt.CryptCATAdminCalcHashFromFileHandle2(admin, handle, ctypes.byref(size), digest, 0):
                    continue
                context = wt.CryptCATAdminEnumCatalogFromHash(admin, digest, size.value, 0, None)
                if not context:
                    continue
                try:
                    cat = CATALOG_INFO()
                    cat.cbStruct = ctypes.sizeof(cat)
                    if not wt.CryptCATCatalogInfoFromContext(context, ctypes.byref(cat), 0):
                        continue
                    info = WINTRUST_CATALOG_INFO()
                    info.cbStruct = ctypes.sizeof(info)
                    info.pcwszCatalogFilePath = cat.wszCatalogFile
                    info.pcwszMemberTag = bytes(digest).hex().upper()
                    info.pcwszMemberFilePath = path
                    info.hMemberFile = handle
                    info.pbCalculatedFileHash = ctypes.cast(digest, ctypes.c_void_p)
                    info.cbCalculatedFileHash = size.value
                    info.hCatAdmin = admin
                    return _verify(WTD_CHOICE_CATALOG, info, path)
                finally:
                    wt.CryptCATAdminReleaseCatalogContext(admin, context, 0)
            finally:
                wt.CryptCATAdminReleaseContext(admin, 0)
    finally:
        k32.CloseHandle(handle)
    return None


def _state(code: int) -> str:
    if code == 0:
        return SIGNED
    if code in NOT_SIGNED:
        return UNSIGNED
    if code in TAMPERED:
        return BROKEN
    if code in DISTRUSTED:
        return UNTRUSTED
    if code == CERT_E_EXPIRED:
        return EXPIRED
    return UNKNOWN


def signature(path: str) -> Signature:
    """The file's signature, from the file itself or from Windows' catalogs, checked without the internet."""
    if sys.platform != "win32":
        return Signature(UNKNOWN)
    try:
        if not os.path.isfile(path):
            return Signature(UNKNOWN)
        info = WINTRUST_FILE_INFO()
        info.cbStruct = ctypes.sizeof(info)
        info.pcwszFilePath = path
        code, signer = _verify(WTD_CHOICE_FILE, info, path)
        if code in NOT_SIGNED:
            found = _catalog(path)
            if found is not None:
                cat_code, cat_signer = found
                return Signature(_state(cat_code), cat_signer, catalog=True, code=cat_code)
        return Signature(_state(code), signer, code=code)
    except Exception:
        log.debug("signature of %s", path, exc_info=True)
        return Signature(UNKNOWN)


# ----------------------------------------------------------------------------- where it came from

ZONES = {0: "this PC", 1: "local network", 2: "trusted", 3: "internet", 4: "restricted"}


@dataclass
class Origin:
    zone: int = -1        # 3 = the internet, 4 = a site marked as dangerous
    host: str = ""        # "github.com"
    referrer: str = ""    # the page that linked to it

    @property
    def from_internet(self) -> bool:
        return self.zone >= 3


def downloaded_from(path: str) -> Origin | None:
    """The "downloaded from the internet" note Windows keeps beside a file, or None if it has none."""
    if sys.platform != "win32":
        return None
    try:
        with open(path + ":Zone.Identifier", "rb") as f:
            raw = f.read(8192)
    except OSError:
        return None
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "replace")
    out = Origin()
    for line in text.splitlines():
        key, _, value = line.partition("=")
        key, value = key.strip().lower(), value.strip()
        if key == "zoneid":
            try:
                out.zone = int(value)
            except ValueError:
                pass
        elif key == "hosturl":
            out.host = _host(value)
        elif key == "referrerurl":
            out.referrer = _host(value)
    return out


def _host(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host
