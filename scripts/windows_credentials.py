"""Read exact Windows Generic Credential targets. No environment fallback or secret output."""
from __future__ import annotations

import argparse
import ctypes
import getpass
import hmac
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline_common import PipelineError, load_json, sha256, timestamp, write_json

NAMES = ('RAKUTEN_APP_ID', 'RAKUTEN_ACCESS_KEY', 'RAKUTEN_AFFILIATE_ID')
PROOF_METHOD = 'official-account-id-match-v1'


@dataclass(repr=False)
class Credentials:
    application_id: str = field(repr=False)
    access_key: str = field(repr=False)
    affiliate_id: str = field(repr=False)

    def values(self):
        return (self.application_id, self.access_key, self.affiliate_id)

    def __repr__(self):
        return 'Credentials([REDACTED])'


def read_windows_credentials() -> Credentials:
    if os.name != 'nt':
        raise PipelineError('WINDOWS_CREDENTIALS_REQUIRED')
    from ctypes import wintypes
    class Credential(ctypes.Structure):
        _fields_ = [('Flags', wintypes.DWORD), ('Type', wintypes.DWORD),
                    ('TargetName', wintypes.LPWSTR), ('Comment', wintypes.LPWSTR),
                    ('LastWritten', wintypes.FILETIME), ('CredentialBlobSize', wintypes.DWORD),
                    ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)), ('Persist', wintypes.DWORD),
                    ('AttributeCount', wintypes.DWORD), ('Attributes', ctypes.c_void_p),
                    ('TargetAlias', wintypes.LPWSTR), ('UserName', wintypes.LPWSTR)]
    api = ctypes.WinDLL('Advapi32.dll', use_last_error=True)
    pointer = ctypes.POINTER(Credential)
    api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(pointer)]
    api.CredReadW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    values = []
    for name in NAMES:
        credential = pointer()
        if not api.CredReadW('noranamani-gadget-deals/' + name, 1, 0, ctypes.byref(credential)):
            raise PipelineError('CREDENTIAL_UNAVAILABLE')
        try:
            raw = ctypes.string_at(credential.contents.CredentialBlob, credential.contents.CredentialBlobSize)
            try:
                value = raw.decode('utf-16-le').rstrip('\0').strip()
            except UnicodeError:
                raise PipelineError('CREDENTIAL_ENCODING_INVALID') from None
            if not value or any(ord(c) < 32 for c in value):
                raise PipelineError('CREDENTIAL_INVALID')
            values.append(value)
        finally:
            api.CredFree(credential)
    return Credentials(*values)


def credential_binding(credentials: Credentials) -> str:
    # Stored only in the user's private proof file, never in collection-status or logs.
    return sha256((credentials.application_id + '\0' + credentials.affiliate_id).encode('utf-8'))


def record_affiliate_proof(path: Path, credentials: Credentials, pasted_id: str, now: datetime) -> None:
    if path.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        raise PipelineError('PROOF_MUST_BE_PRIVATE')
    if not hmac.compare_digest(pasted_id.strip(), credentials.affiliate_id):
        raise PipelineError('AFFILIATE_ACCOUNT_ID_MISMATCH')
    write_json(path, {'schemaVersion': 1, 'method': PROOF_METHOD,
                     'credentialBinding': credential_binding(credentials),
                     'verifiedAt': now.isoformat(), 'expiresAt': (now + timedelta(days=30)).isoformat()})


def affiliate_proof(path: Path | None, credentials: Credentials, now: datetime) -> dict:
    if path is None or not path.is_file():
        return {'verified': False, 'expiresAt': None}
    proof = load_json(path)
    if (proof.get('schemaVersion') != 1 or proof.get('method') != PROOF_METHOD or
            not hmac.compare_digest(str(proof.get('credentialBinding', '')), credential_binding(credentials))):
        raise PipelineError('AFFILIATE_PROOF_INVALID')
    verified, expiry = timestamp(proof.get('verifiedAt')), timestamp(proof.get('expiresAt'))
    if verified > now or not verified < expiry <= verified + timedelta(days=30) or now >= expiry:
        raise PipelineError('AFFILIATE_PROOF_EXPIRED')
    return {'verified': True, 'expiresAt': expiry.isoformat()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--verify-affiliate', type=Path, metavar='PRIVATE_PROOF_FILE')
    args = parser.parse_args()
    try:
        credentials = read_windows_credentials()
        if args.verify_affiliate:
            import sys
            if not sys.stdin.isatty():
                raise PipelineError('INTERACTIVE_TERMINAL_REQUIRED')
            # The operator must check their signed-in official account confirmation page.
            pasted = getpass.getpass('Official account Affiliate ID (hidden; not a link ID): ')
            record_affiliate_proof(args.verify_affiliate, credentials, pasted, datetime.now(timezone.utc))
            print('AFFILIATE_ACCOUNT_CONFIRMATION_RECORDED')
        else:
            print(json.dumps({'windowsCredentialsReadable': {name: True for name in NAMES}}))
        return 0
    except PipelineError as exc:
        print('CREDENTIAL_CHECK_STOPPED: ' + str(exc))
        return 1
    except Exception:
        print('CREDENTIAL_CHECK_STOPPED: INTERNAL_ERROR')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
