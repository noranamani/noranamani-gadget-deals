"""Context-aware output checks. No values or URLs in diagnostics; no URL rewriting."""
from __future__ import annotations

import copy
import html
import re
from urllib.parse import parse_qsl, unquote, urlsplit

from pipeline_common import PipelineError, json_bytes

TOKEN = re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})')
FIELDS = {'affiliateUrl', 'itemUrl', 'itemName', 'shopName', 'image', 'url'}
QUERY_KEYS = {'pc', 'm', 'link_type', 'ut', 'rafcid', 'scid', 'sc2id'}


def decoded(text):
    for _ in range(8):
        new = html.unescape(unquote(text))
        new = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m[1], 16)), new)
        if new == text:
            return text
        text = new
    raise PipelineError('OUTPUT_ENCODING_DEPTH_EXCEEDED')


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from strings(key)
            yield from strings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from strings(child)


def affiliate_url(url: str) -> bool:
    try:
        p = urlsplit(url)
        return (isinstance(url, str) and 0 < len(url) <= 8192
                and not any(ord(c) < 33 or ord(c) == 127 for c in url) and '\\' not in url
                and p.scheme == 'https' and p.hostname == 'hb.afl.rakuten.co.jp'
                and p.path.startswith('/hgc/') and not p.username and not p.password
                and p.port in (None, 443) and not p.fragment)
    except (TypeError, ValueError):
        return False


class OutputViolation(PipelineError):
    def __init__(self, kind, context, field):
        # All three labels are chosen by code, never copied from a response.
        self.diagnostic = {'credentialKind': kind, 'context': context, 'field': field}
        super().__init__('SECRET_IN_OUTPUT_' + kind.upper() + '_' + context.upper() + '_' + field.upper())


class OutputPolicy:
    """Trust is scoped to URLs accepted from this authenticated API client's response."""
    def __init__(self, credentials):
        self.__values = dict(zip(('application_id', 'access_key', 'affiliate_id'), credentials.values()))
        self.__approved = set()

    def __repr__(self):
        return 'OutputPolicy([REDACTED])'

    def reject(self, value, context, field='other', kinds=None):
        rendered = json_bytes(value).decode('utf-8')
        # Inspect original string leaves too: JSON serialization escapes literal backslashes.
        for raw in (rendered, *strings(value)):
            texts = (raw, decoded(raw))
            if any(TOKEN.search(t) for t in texts):
                raise OutputViolation('token_pattern', context, field)
            for kind in kinds or self.__values:
                secret = self.__values[kind]
                if secret and any(secret in t for t in texts):
                    raise OutputViolation(kind, context, field)

    def check_tracking_url(self, url):
        if not affiliate_url(url):
            raise PipelineError('AFFILIATE_LINK_INVALID')
        self.reject(url, 'tracking', 'url', ('access_key',))
        p = urlsplit(url)
        try:
            pairs = parse_qsl(p.query, keep_blank_values=True, strict_parsing=True,
                              max_num_fields=32, encoding='utf-8', errors='strict')
        except (ValueError, UnicodeError):
            raise PipelineError('TRACKING_QUERY_INVALID') from None
        if len({k for k, _ in pairs}) != len(pairs):
            raise PipelineError('TRACKING_QUERY_DUPLICATE')
        raw_pairs = p.query.split('&') if p.query else []
        app, aff = self.__values['application_id'], self.__values['affiliate_id']
        view_pairs, exceptions = [], 0
        for (key, value), raw in zip(pairs, raw_pairs):
            raw_key, _, raw_value = raw.partition('=')
            if (key == 'rafcid' and raw_key == key and app
                    and value == raw_value == 'wsc_i_is_' + app):
                # Only this observed RWS item-search tracking slot, not arbitrary query data.
                self.reject(value, 'tracking', 'rafcid', ('access_key', 'affiliate_id'))
                view_pairs.append((key, '[VALIDATED_RWS_ITEM_TRACKING]'))
                exceptions += 1
            else:
                view_pairs.append((key, value))
        path = p.path
        if aff and path == '/hgc/' + aff + '/':
            self.reject(path, 'tracking', 'affiliate_id', ('application_id', 'access_key'))
            path = '/hgc/[VALIDATED_AFFILIATE_TRACKING]/'
            exceptions += 1
        if exceptions:
            if any(k not in QUERY_KEYS for k, _ in pairs):
                raise PipelineError('TRACKING_QUERY_UNSUPPORTED')
            destinations = dict(pairs)
            try:
                pc = urlsplit(destinations.get('pc', ''))
                valid_pc = (pc.scheme == 'https' and pc.hostname == 'item.rakuten.co.jp'
                            and not pc.username and not pc.password and pc.port in (None, 443))
                if not valid_pc:
                    raise PipelineError('TRACKING_DESTINATION_INVALID')
                if destinations.get('m'):
                    mobile = urlsplit(destinations['m'])
                    if (mobile.scheme not in ('http', 'https') or mobile.hostname != 'm.rakuten.co.jp'
                            or mobile.username or mobile.password or mobile.port not in (None, 80, 443)):
                        raise PipelineError('TRACKING_DESTINATION_INVALID')
            except ValueError:
                raise PipelineError('TRACKING_DESTINATION_INVALID') from None
        # The inspection view is separate from the original URL; nothing is rewritten.
        self.reject({'scheme': p.scheme, 'authority': p.netloc, 'path': path,
                     'query': view_pairs, 'fragment': p.fragment}, 'tracking', 'url')

    def check_api_items(self, rows):
        approved = set()
        for row in rows:
            if not isinstance(row, dict):
                raise PipelineError('PRODUCT_ROW_INVALID')
            url = row.get('affiliateUrl')
            self.check_tracking_url(url)
            for field, value in row.items():
                self.reject(field, 'api', 'other')
                if field == 'affiliateUrl' or field == 'itemUrl' and value == url:
                    self.check_tracking_url(value)
                else:
                    self.reject(value, 'api', field if field in FIELDS else 'other')
            approved.add(url)
        # A partial response never grants trust to earlier rows.
        self.__approved.update(approved)

    def check_products(self, products):
        view = copy.deepcopy(products)
        try:
            rows = [row for category in view['categories'] for row in category['items']]
            rows += list(view.get('deals', []))
            inspected = set()
            for row in rows:
                # build_products shares an item object between categories and deals.
                if id(row) in inspected:
                    continue
                url = row['url']
                if url not in self.__approved:
                    raise PipelineError('UNVERIFIED_TRACKING_URL')
                self.check_tracking_url(url)
                row['url'] = '[VALIDATED_API_RETURNED_URL]'
                inspected.add(id(row))
        except (KeyError, TypeError):
            raise PipelineError('PRODUCT_OUTPUT_SCHEMA_INVALID') from None
        self.reject(view, 'products')
