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

from pipeline_common import PipelineError, json_bytes, load_json, sha256, timestamp, write_json

NAMES = ('RAKUTEN_APP_ID', 'RAKUTEN_ACCESS_KEY', 'RAKUTEN_AFFILIATE_ID')
PROOF_METHOD = 'official-account-id-match-v1'
MAINTENANCE_METHOD = 'operator-approved-rakuten-ui-portal-maintenance-v1'
MAINTENANCE_DAYS = 7
MAINTENANCE_FILE = 'affiliate-maintenance.json'
# One-off exception for the 2026-11-09 UI portal outage; cannot be re-used for
# subsequent expiry cycles without a new reviewed code change.
MAINTENANCE_ABSOLUTE_END = datetime(2026, 11, 27, 5, 37, 21, 374999, tzinfo=timezone.utc)
# Separate, non-renewable exception for the 2026-11-24 official notice.
FOLLOWUP_MODE = 'maintenance_continuity_20261124'
FOLLOWUP_METHOD = 'operator-approved-rakuten-ui-portal-maintenance-20261124-v2'
FOLLOWUP_FILE = 'affiliate-maintenance-20261124.json'
FOLLOWUP_START = datetime(2026, 11, 24, 15, 0, tzinfo=timezone.utc)
FOLLOWUP_END = datetime(2026, 12, 15, 15, 0, tzinfo=timezone.utc)
FOLLOWUP_ORIGINAL_EXPIRY = MAINTENANCE_ABSOLUTE_END - timedelta(days=7)



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


def _validated_proof(path: Path, credentials: Credentials, now: datetime) -> tuple[dict, datetime]:
    """Validate the unchanged original, even after it has expired."""
    proof = load_json(path)
    if (not isinstance(proof, dict) or proof.get('schemaVersion') != 1
            or proof.get('method') != PROOF_METHOD
            or not hmac.compare_digest(str(proof.get('credentialBinding', '')),
                                       credential_binding(credentials))):
        raise PipelineError('AFFILIATE_PROOF_INVALID')
    verified, expiry = timestamp(proof.get('verifiedAt')), timestamp(proof.get('expiresAt'))
    if verified > now or not verified < expiry <= verified + timedelta(days=30):
        raise PipelineError('AFFILIATE_PROOF_INVALID')
    return proof, expiry


def maintenance_path(proof_path: Path) -> Path:
    """Marker lives next to the private original proof; never in Git."""
    return proof_path.with_name(MAINTENANCE_FILE)


def authorize_maintenance_continuity(proof_path: Path, credentials: Credentials,
                                     now: datetime) -> dict:
    """Single-use, local human authorization. Does not edit the original proof."""
    proof_path = Path(proof_path)
    code_root = Path(__file__).resolve().parents[1]
    marker = maintenance_path(proof_path)
    if (proof_path.is_symlink() or marker.is_symlink()
            or proof_path.resolve().is_relative_to(code_root)
            or marker.resolve().is_relative_to(code_root)):
        raise PipelineError('MAINTENANCE_PATH_MUST_BE_PRIVATE')
    if not proof_path.is_file():
        raise PipelineError('AFFILIATE_PROOF_REQUIRED')
    proof, expiry = _validated_proof(proof_path, credentials, now)
    end = expiry + timedelta(days=MAINTENANCE_DAYS)
    if now < expiry - timedelta(days=30) or now >= end or end > MAINTENANCE_ABSOLUTE_END:
        raise PipelineError('MAINTENANCE_WINDOW_CLOSED')
    # Never overwrite, roll forward, or refresh a previous authorization.
    if marker.exists():
        raise PipelineError('MAINTENANCE_ALREADY_AUTHORIZED')
    record = {'schemaVersion': 1, 'method': MAINTENANCE_METHOD,
              'credentialBinding': credential_binding(credentials),
              'proofVerifiedAt': proof['verifiedAt'],
              'proofExpiresAt': proof['expiresAt'],
              'authorizedAt': now.isoformat(), 'expiresAt': end.isoformat()}
    try:
        with marker.open('xb') as handle:
            handle.write(json_bytes(record))
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise PipelineError('MAINTENANCE_ALREADY_AUTHORIZED') from None
    return record


def _maintenance_continuity(proof_path: Path, credentials: Credentials,
                            now: datetime, proof: dict, original_expiry: datetime) -> dict:
    marker_path = maintenance_path(proof_path)
    if marker_path.is_symlink() or not marker_path.is_file():
        raise PipelineError('AFFILIATE_PROOF_EXPIRED')
    marker = load_json(marker_path)
    if (not isinstance(marker, dict)
            or marker.get('schemaVersion') != 1
            or marker.get('method') != MAINTENANCE_METHOD
            or not hmac.compare_digest(str(marker.get('credentialBinding', '')),
                                       credential_binding(credentials))
            or marker.get('proofVerifiedAt') != proof.get('verifiedAt')
            or marker.get('proofExpiresAt') != proof.get('expiresAt')):
        raise PipelineError('MAINTENANCE_AUTHORIZATION_INVALID')
    authorized = timestamp(marker.get('authorizedAt'))
    end = timestamp(marker.get('expiresAt'))
    if not (original_expiry - timedelta(days=30) <= authorized
            < original_expiry + timedelta(days=MAINTENANCE_DAYS)
            and end == original_expiry + timedelta(days=MAINTENANCE_DAYS)
            and end <= MAINTENANCE_ABSOLUTE_END
            and now >= original_expiry and authorized <= now < end):
        raise PipelineError('MAINTENANCE_AUTHORIZATION_EXPIRED')
    return {'verified': True, 'mode': 'maintenance_continuity',
            'expiresAt': end.isoformat(),
            'originalProofExpiresAt': original_expiry.isoformat(),
            'authorizedAt': authorized.isoformat()}


def _followup_path(proof_path: Path) -> Path:
    return proof_path.with_name(FOLLOWUP_FILE)


def _followup_private_paths(proof_path: Path) -> tuple[Path, Path]:
    prior, marker = maintenance_path(proof_path), _followup_path(proof_path)
    code_root = Path(__file__).resolve().parents[1]
    if any(p.is_symlink() or p.resolve().is_relative_to(code_root)
           for p in (proof_path, prior, marker)):
        raise PipelineError('MAINTENANCE_FOLLOWUP_PATH_MUST_BE_PRIVATE')
    return prior, marker


def authorize_maintenance_followup(proof_path: Path, credentials: Credentials,
                                   now: datetime) -> dict:
    """Separate local authorization; never edit original proof or v1 marker."""
    proof_path = Path(proof_path)
    prior, marker = _followup_private_paths(proof_path)
    proof, expiry = _validated_proof(proof_path, credentials, now)
    if expiry != FOLLOWUP_ORIGINAL_EXPIRY or not FOLLOWUP_START <= now < MAINTENANCE_ABSOLUTE_END:
        raise PipelineError('MAINTENANCE_FOLLOWUP_AUTHORIZATION_WINDOW_CLOSED')
    if marker.exists():
        raise PipelineError('MAINTENANCE_FOLLOWUP_ALREADY_AUTHORIZED')
    old = _maintenance_continuity(proof_path, credentials, now, proof, expiry)
    record = {'schemaVersion': 1, 'method': FOLLOWUP_METHOD,
              'credentialBinding': credential_binding(credentials),
              'proofVerifiedAt': proof['verifiedAt'], 'proofExpiresAt': proof['expiresAt'],
              'priorMarkerSha256': sha256(prior.read_bytes()),
              'priorAuthorizedAt': old['authorizedAt'],
              'authorizedAt': now.isoformat(), 'expiresAt': FOLLOWUP_END.isoformat()}
    try:
        with marker.open('xb') as handle:
            handle.write(json_bytes(record))
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise PipelineError('MAINTENANCE_FOLLOWUP_ALREADY_AUTHORIZED') from None
    return record


def _maintenance_followup(proof_path: Path, credentials: Credentials, now: datetime,
                          proof: dict, expiry: datetime) -> dict:
    prior, marker_path = _followup_private_paths(proof_path)
    marker = load_json(marker_path)
    if (not isinstance(marker, dict) or marker.get('schemaVersion') != 1
            or marker.get('method') != FOLLOWUP_METHOD
            or not hmac.compare_digest(str(marker.get('credentialBinding', '')),
                                       credential_binding(credentials))
            or marker.get('proofVerifiedAt') != proof.get('verifiedAt')
            or marker.get('proofExpiresAt') != proof.get('expiresAt')
            or expiry != FOLLOWUP_ORIGINAL_EXPIRY):
        raise PipelineError('MAINTENANCE_FOLLOWUP_INVALID')
    authorized = timestamp(marker.get('authorizedAt'))
    end = timestamp(marker.get('expiresAt'))
    if not (FOLLOWUP_START <= authorized < MAINTENANCE_ABSOLUTE_END
            and authorized <= now < end and end == FOLLOWUP_END):
        raise PipelineError('MAINTENANCE_FOLLOWUP_EXPIRED_OR_INVALID')
    # Validate the v1 marker at the actual v2 authorization time.
    # This is historical evidence validation, not a runtime clock change.
    old = _maintenance_continuity(proof_path, credentials, authorized, proof, expiry)
    if (marker.get('priorAuthorizedAt') != old['authorizedAt']
            or not hmac.compare_digest(str(marker.get('priorMarkerSha256', '')),
                                       sha256(prior.read_bytes()))):
        raise PipelineError('MAINTENANCE_FOLLOWUP_PRIOR_MARKER_CHANGED')
    return {'verified': True, 'mode': FOLLOWUP_MODE, 'expiresAt': end.isoformat(),
            'originalProofExpiresAt': expiry.isoformat(),
            'priorAuthorizedAt': old['authorizedAt'], 'authorizedAt': authorized.isoformat()}


def affiliate_proof(path: Path | None, credentials: Credentials, now: datetime) -> dict:
    if path is None or not path.is_file():
        return {'verified': False, 'expiresAt': None}
    proof, expiry = _validated_proof(path, credentials, now)
    if now < expiry:
        # A new genuine confirmation always takes precedence over any old marker.
        return {'verified': True, 'expiresAt': expiry.isoformat(), 'mode': 'official'}
    followup = _followup_path(path)
    if followup.exists() or followup.is_symlink():
        return _maintenance_followup(path, credentials, now, proof, expiry)
    return _maintenance_continuity(path, credentials, now, proof, expiry)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--verify-affiliate', type=Path, metavar='PRIVATE_PROOF_FILE')
    parser.add_argument('--check-affiliate-proof', type=Path, metavar='PRIVATE_PROOF_FILE',
                        help='Read-only expiry and credential binding check; no API calls')
    parser.add_argument('--authorize-maintenance', type=Path, metavar='PRIVATE_PROOF_FILE',
                        help='One-time interactive 7-day maintenance authorization')
    parser.add_argument('--authorize-maintenance-20261124', type=Path, metavar='PRIVATE_PROOF_FILE',
                        help='Separate one-time interactive authorization for the Nov 24 notice')
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
        elif args.authorize_maintenance:
            import sys
            if not sys.stdin.isatty():
                raise PipelineError('INTERACTIVE_TERMINAL_REQUIRED')
            print('Rakuten UI Portal maintenance only. The original proof will NOT be renewed.')
            print('This permits collection only until 7 days after the original proof expires.')
            answer = input('Type MAINTENANCE to approve this one-time exception: ')
            if not hmac.compare_digest(answer, 'MAINTENANCE'):
                raise PipelineError('MAINTENANCE_NOT_CONFIRMED')
            result = authorize_maintenance_continuity(
                args.authorize_maintenance, credentials, datetime.now(timezone.utc))
            print(json.dumps({'affiliateMaintenance': 'authorized',
                              'expiresAt': result['expiresAt']}))
        elif args.authorize_maintenance_20261124:
            import sys
            if not sys.stdin.isatty():
                raise PipelineError('INTERACTIVE_TERMINAL_REQUIRED')
            print('Official notice 2026-11-24: https://affiliate.rakuten.co.jp/info/')
            print('Confirm the portal remains unavailable, the account/IDs are unchanged,')
            print('and approve continuity only through Dec 15 JST. No genuine proof is renewed.')
            answer = input('Type MAINTENANCE-20261124 to authorize once: ')
            if not hmac.compare_digest(answer, 'MAINTENANCE-20261124'):
                raise PipelineError('MAINTENANCE_NOT_CONFIRMED')
            result = authorize_maintenance_followup(
                args.authorize_maintenance_20261124, credentials, datetime.now(timezone.utc))
            print(json.dumps({'affiliateMaintenance': FOLLOWUP_MODE,
                              'expiresAt': result['expiresAt']}))
        elif args.check_affiliate_proof:
            result = affiliate_proof(args.check_affiliate_proof, credentials, datetime.now(timezone.utc))
            if not result['verified']:
                raise PipelineError('AFFILIATE_CONFIRMATION_REQUIRED')
            print(json.dumps({'affiliateProof': (result['mode']
                                               if result.get('mode') in ('maintenance_continuity', FOLLOWUP_MODE)
                                               else 'valid'),
                              'expiresAt': result['expiresAt']}))
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
