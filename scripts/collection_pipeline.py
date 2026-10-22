"""Validate a complete generation before saving it; never modifies checkout data or pushes."""
from __future__ import annotations

import argparse
import copy
import math
import re
from datetime import datetime, timezone
from pathlib import Path

from fetch_rakuten import build_products, unwrap_items
from pipeline_common import (PipelineError, SnapshotStore, assert_no_secrets, digest, history_preserved,
                             json_bytes, load_json, sha256, source_hashes, validate_history)
from rakuten_client import RakutenClient, affiliate_url
from windows_credentials import read_windows_credentials, affiliate_proof
from output_policy import OutputPolicy


def validate_queries(queries):
    if not isinstance(queries, list) or not 1 <= len(queries) <= 64:
        raise PipelineError('QUERIES_INVALID')
    ids = []
    for row in queries:
        if (not isinstance(row, dict) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', str(row.get('id', '')))
                or not isinstance(row.get('name'), str) or not row['name'].strip()
                or not isinstance(row.get('keyword'), str) or not row['keyword'].strip()):
            raise PipelineError('QUERY_ROW_INVALID')
        low, high = row.get('minPrice', 1), row.get('maxPrice', 99999999)
        if type(low) is not int or type(high) is not int or not 0 < low < high:
            raise PipelineError('QUERY_PRICE_INVALID')
        ids.append(row['id'])
    if len(set(ids)) != len(ids):
        raise PipelineError('QUERY_DUPLICATE_ID')


def collect_generation(root: Path, state: Path, requester, verification: dict,
                       now: datetime, source_commit: str, secrets=(), checkpoint=lambda phase: None,
                       output_policy: OutputPolicy | None = None) -> dict:
    root, state = root.resolve(), state.resolve()
    if state.is_relative_to(root) or root.is_relative_to(state):
        raise PipelineError('STATE_MUST_BE_SEPARATE')
    if not re.fullmatch(r'[0-9a-f]{40}', source_commit) or now.tzinfo is None:
        raise PipelineError('GENERATION_CONTEXT_INVALID')
    expected = source_hashes(root)
    history = load_json(root / 'data/history.json')
    validate_history(history)
    queries, cfg = load_json(root / 'config/queries.json'), load_json(root / 'config/site.json')
    validate_queries(queries)
    if not isinstance(cfg, dict):
        raise PipelineError('SITE_CONFIG_INVALID')
    if any(row['date'] > now.astimezone(timezone.utc).date().isoformat() for rows in history.values() for row in rows):
        raise PipelineError('HISTORY_FROM_FUTURE')
    previous = load_json(root / 'data/products.json')
    if not isinstance(previous, dict) or not isinstance(previous.get('categories'), list):
        raise PipelineError('PREVIOUS_PRODUCTS_INVALID')
    try:
        previous_counts = {c['id']: len(c['items']) for c in previous['categories']}
    except (KeyError, TypeError):
        raise PipelineError('PREVIOUS_PRODUCTS_INVALID') from None
    events = []

    def guarded(params):
        category = queries[len(events)//2]['id']
        payload = requester(params)
        if not isinstance(payload, dict) or payload.get('error') or payload.get('errors'):
            raise PipelineError('API_PAYLOAD_INVALID')
        rows = unwrap_items(payload)
        if not rows:
            raise PipelineError('CATEGORY_EMPTY_RESPONSE')
        for row in rows:
            if (not row.get('itemCode') or not row.get('itemName') or type(row.get('itemPrice')) is not int
                    or row['itemPrice'] <= 0 or not affiliate_url(str(row.get('affiliateUrl', '')))):
                raise PipelineError('PRODUCT_ROW_INVALID')
            try:
                if not math.isfinite(float(row.get('reviewAverage') or 0)) or int(row.get('reviewCount') or 0) < 0:
                    raise ValueError()
            except (ValueError, TypeError, OverflowError):
                raise PipelineError('PRODUCT_NUMERIC_INVALID') from None
        events.append({'categoryId': category, 'sort': params['sort'], 'responseItems': len(rows),
                       'uniqueCodes': sorted({str(r['itemCode']) for r in rows})})
        return payload

    products, updated_history = build_products(cfg, queries, history, guarded, now.astimezone(timezone.utc))
    categories = []
    if [c['id'] for c in products['categories']] != [q['id'] for q in queries]:
        raise PipelineError('CATEGORY_COVERAGE_INVALID')
    for category in products['categories']:
        count = len({item['code'] for item in category['items']})
        minimum = max(6, math.ceil(previous_counts.get(category['id'], 0) / 2))
        if count < minimum:
            raise PipelineError('CATEGORY_ABNORMALLY_SMALL')
        unique = {code for e in events if e['categoryId'] == category['id'] for code in e['uniqueCodes']}
        categories.append({'id': category['id'], 'fetchedUnique': len(unique), 'displayed': count})
    preservation = history_preserved(history, updated_history)
    products['methodology']['historyRetention'] = 'preserve-all'
    status = {'schemaVersion': 1, 'collectedAt': products['updatedAt'], 'sourceCommit': source_commit,
              'productsSha256': sha256(json_bytes(products)), 'historySha256': sha256(json_bytes(updated_history)),
              'queriesSha256': expected['config/queries.json'], 'siteConfigSha256': expected['config/site.json'],
              'affiliateVerified': verification.get('verified') is True,
              'affiliateVerificationExpiresAt': verification.get('expiresAt'),
              'categories': categories, 'historyPreservation': preservation}
    if output_policy is not None:
        if not isinstance(output_policy, OutputPolicy):
            raise PipelineError('OUTPUT_POLICY_INVALID')
        output_policy.check_products(products)
        output_policy.reject(updated_history, 'history')
        output_policy.reject(status, 'status')
    else:
        assert_no_secrets(products, secrets)
    for value in (updated_history, status):
        assert_no_secrets(value, secrets)
    generation = SnapshotStore(state).commit(root, products, updated_history, status, expected, checkpoint)
    return {'generation': str(generation), 'status': status}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--proxy', required=True, help='HTTP proxy URL without authentication')
    parser.add_argument('--ca-bundle', type=Path)
    parser.add_argument('--affiliate-proof', type=Path)
    parser.add_argument('--source-commit', required=True)
    args = parser.parse_args()
    try:
        from pipeline_common import exclusive_lock
        now = datetime.now(timezone.utc)
        with exclusive_lock(args.state.resolve()):
            credentials = read_windows_credentials()
            verification = affiliate_proof(args.affiliate_proof, credentials, now)
            queries = load_json(args.root / 'config/queries.json')
            validate_queries(queries)
            client = RakutenClient(credentials, args.proxy, 2 * len(queries), args.ca_bundle)
            result = collect_generation(args.root, args.state, client, verification, now,
                                        args.source_commit, credentials.values(), output_policy=client.output_policy)
            print('COLLECTION_PREPARED: categories=' + str(len(result['status']['categories'])))
        return 0
    except PipelineError as exc:
        print('COLLECTION_STOPPED: ' + str(exc))
        return 1
    except Exception:
        print('COLLECTION_STOPPED: INTERNAL_ERROR')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
