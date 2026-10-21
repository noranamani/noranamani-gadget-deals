"""Verified HTTPS through an explicit corporate proxy, bounded and without API retries."""
from __future__ import annotations

import ipaddress
import ssl
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, ProxyHandler, HTTPSHandler, HTTPRedirectHandler, build_opener, proxy_bypass

from pipeline_common import PipelineError, parse_json, assert_no_secrets
from windows_credentials import Credentials

ENDPOINT = 'https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701'


def validate_proxy(url: str) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme != 'http' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise PipelineError('PROXY_CONFIGURATION_INVALID')
    try:
        if not parsed.port:
            raise ValueError()
    except ValueError:
        raise PipelineError('PROXY_CONFIGURATION_INVALID') from None


def local_host(host: str) -> bool:
    if host.lower() == 'localhost' or '.' not in host and ':' not in host:
        return True
    try:
        address = ipaddress.ip_address(host)
        return address.is_loopback or address in ipaddress.ip_network('192.168.2.0/24') or address in ipaddress.ip_network('192.168.3.0/24')
    except ValueError:
        return False


class CorporateProxy(ProxyHandler):
    def proxy_open(self, request, proxy, kind):
        host = urlsplit(request.full_url).hostname or ''
        if local_host(host):
            return None
        if proxy_bypass(host):
            raise PipelineError('EXTERNAL_PROXY_BYPASS')
        return super().proxy_open(request, proxy, kind)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def affiliate_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return (parts.scheme == 'https' and parts.hostname == 'hb.afl.rakuten.co.jp'
                and parts.path.startswith('/hgc/') and not parts.username and not parts.password
                and parts.port in (None, 443))
    except ValueError:
        return False


class RakutenClient:
    def __init__(self, credentials: Credentials, proxy_url: str, request_budget: int,
                 ca_bundle: Path | None = None, interval=1.3, opener=None, clock=time.monotonic, sleep=time.sleep):
        validate_proxy(proxy_url)
        if interval < 1.0 or request_budget < 1:
            raise PipelineError('REQUEST_POLICY_INVALID')
        self.credentials, self.budget, self.interval = credentials, request_budget, interval
        self.clock, self.sleep, self.last = clock, sleep, None
        self.events = []
        self.context = ssl.create_default_context()
        if ca_bundle:
            self.context.load_verify_locations(cafile=str(ca_bundle))
        self.opener = opener or build_opener(CorporateProxy({'http': proxy_url, 'https': proxy_url}),
            HTTPSHandler(context=self.context), NoRedirect())

    def __call__(self, params: dict) -> dict:
        if len(self.events) >= self.budget:
            raise PipelineError('REQUEST_BUDGET_EXCEEDED')
        if self.last is not None:
            while (remaining := self.interval - (self.clock() - self.last)) > 0:
                self.sleep(remaining)
        query = {**params, 'applicationId': self.credentials.application_id,
                 'affiliateId': self.credentials.affiliate_id}
        request = Request(ENDPOINT + '?' + urlencode(query), headers={
            'accessKey': self.credentials.access_key, 'User-Agent': 'noranamani-gadget-deals/3.0'})
        event = {'request': len(self.events) + 1}
        self.events.append(event)
        self.last = self.clock()
        try:
            with self.opener.open(request, timeout=30) as response:
                event['httpStatus'] = response.status
                if response.status != 200:
                    raise PipelineError('RAKUTEN_HTTP_' + str(response.status))
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise PipelineError('RESPONSE_TOO_LARGE')
                payload = parse_json(raw)
        except HTTPError as exc:
            event['httpStatus'] = exc.code
            exc.close()
            raise PipelineError('RAKUTEN_HTTP_' + str(event['httpStatus'])) from None
        except (URLError, TimeoutError, ssl.SSLError):
            raise PipelineError('RAKUTEN_NETWORK_ERROR') from None
        if not isinstance(payload, dict) or payload.get('error') or payload.get('errors'):
            raise PipelineError('RAKUTEN_PAYLOAD_INVALID')
        rows = payload.get('items') or payload.get('Items') or []
        if not isinstance(rows, list) or not rows:
            raise PipelineError('CATEGORY_EMPTY_RESPONSE')
        clean = []
        allowed = set(params['elements'].split(','))
        for row in rows:
            if not isinstance(row, dict):
                raise PipelineError('PRODUCT_ROW_INVALID')
            row = row.get('Item', row)
            if not affiliate_url(str(row.get('affiliateUrl') or '')):
                raise PipelineError('AFFILIATE_LINK_INVALID')
            # Preserve the API-returned URL byte-for-byte. Never infer an item URL from its code.
            clean.append({name: value for name, value in row.items() if name in allowed})
        result = {'items': clean}
        assert_no_secrets(result, self.credentials.values())
        event['items'] = len(clean)
        return result
