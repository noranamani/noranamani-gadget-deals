"""Offline fixtures only. Never read real credentials or access an external API."""
from __future__ import annotations

import copy
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import quote

import test_windows_pipeline as fixtures
from collection_pipeline import collect_generation
from output_policy import OutputPolicy, OutputViolation
from pipeline_common import PipelineError, assert_no_secrets, json_bytes, load_json, source_hashes
from rakuten_client import RakutenClient
from windows_credentials import Credentials

CREDS = fixtures.CREDS
LINK = ('https://hb.afl.rakuten.co.jp/hgc/public-link-id/?'
        'pc=https%3A%2F%2Fitem.rakuten.co.jp%2Ffixture%2Fproduct%2F&'
        'm=http%3A%2F%2Fm.rakuten.co.jp%2Ffixture%2Fproduct%2F&'
        'rafcid=wsc_i_is_' + CREDS.application_id)


def row(url=LINK):
    return {**fixtures.response(1)['items'][0], 'affiliateUrl': url, 'itemUrl': url}


def products(url=LINK):
    return {'categories': [{'items': [{'url': url, 'name': 'fixture'}]}],
            'deals': [{'url': url}]}


class OutputPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = OutputPolicy(CREDS)

    def reject_row(self, value):
        with self.assertRaises(PipelineError) as caught:
            self.policy.check_api_items([value])
        message = str(caught.exception)
        for secret in CREDS.values():
            self.assertNotIn(secret, message)
        self.assertNotIn('https://', message)
        return caught.exception

    def test_observed_tracking_slot_preserves_literal_url(self):
        data = row()
        before = copy.deepcopy(data)
        self.policy.check_api_items([data])
        output = products()
        self.policy.check_products(output)
        self.assertEqual(data, before)
        self.assertEqual(output, products())

    def test_generic_guard_remains_strict_for_same_url(self):
        with self.assertRaisesRegex(PipelineError, 'SECRET_IN_OUTPUT'):
            assert_no_secrets(row(), CREDS.values())

    def test_shared_category_and_deal_object_checked_once_without_mutation(self):
        self.policy.check_api_items([row()])
        data = products()
        data['deals'] = [data['categories'][0]['items'][0]]
        self.policy.check_products(data)
        self.assertEqual(data['deals'][0]['url'], LINK)

    def test_affiliate_id_only_in_exact_tracking_path(self):
        url = LINK.replace('/hgc/public-link-id/', '/hgc/' + CREDS.affiliate_id + '/')
        self.policy.check_api_items([row(url)])
        self.policy.check_products(products(url))
        for url in (LINK + '&scid=' + CREDS.affiliate_id,
                    LINK.replace('public-link-id', 'prefix-' + CREDS.affiliate_id)):
            with self.subTest(case='wrong-affiliate-slot'):
                self.reject_row(row(url))

    def test_access_key_is_never_exempt_in_any_slot(self):
        for url in (LINK + '&scid=' + CREDS.access_key,
                    LINK.replace(CREDS.application_id, CREDS.access_key),
                    LINK.replace('public-link-id', CREDS.access_key),
                    LINK.replace('product', CREDS.access_key)):
            with self.subTest(case='access-key-in-url'):
                exc = self.reject_row(row(url))
                self.assertIsInstance(exc, OutputViolation)
                self.assertEqual(exc.diagnostic['credentialKind'], 'access_key')

    def test_application_id_in_wrong_query_or_path_fails(self):
        for url in (LINK + '&scid=' + CREDS.application_id,
                    LINK.replace('public-link-id', CREDS.application_id),
                    LINK.replace('product', CREDS.application_id)):
            with self.subTest(case='wrong-app-slot'):
                self.reject_row(row(url))

    def test_tracking_value_requires_exact_prefix_and_suffix(self):
        for tracking in (CREDS.application_id, 'other_' + CREDS.application_id,
                         'wsc_i_is_' + CREDS.application_id + '_suffix'):
            self.reject_row(row(LINK.replace('wsc_i_is_' + CREDS.application_id, tracking)))

    def test_tracking_key_must_be_literal(self):
        self.reject_row(row(LINK.replace('rafcid=', '%72afcid=')))

    def test_encoded_tracking_value_is_not_exempt(self):
        encoded = ''.join('%{:02X}'.format(ord(c)) for c in CREDS.application_id)
        self.reject_row(row(LINK.replace(CREDS.application_id, encoded)))

    def test_encoded_secrets_blocked_in_other_fields(self):
        for secret in CREDS.values():
            encoded = ''.join('%{:02X}'.format(ord(c)) for c in secret)
            variants = (encoded, quote(encoded), ''.join('&#{};'.format(ord(c)) for c in secret),
                        ''.join('\\u{:04x}'.format(ord(c)) for c in secret))
            for value in variants:
                with self.subTest(encoding='encoded-fixture'):
                    self.reject_row({**row(), 'itemName': value})

    def test_encoded_access_key_blocked_in_destination(self):
        encoded = ''.join('%{:02X}'.format(ord(c)) for c in CREDS.access_key)
        self.reject_row(row(LINK.replace('product', quote(encoded))))

    def test_secret_non_url_fields_and_keys_fail(self):
        for field in ('itemName', 'shopName', 'mediumImageUrls', 'unknown'):
            for secret in CREDS.values():
                self.reject_row({**row(), field: secret})
        self.reject_row({**row(), CREDS.access_key: 'fixture'})

    def test_entire_approved_url_in_item_name_still_fails(self):
        self.policy.check_api_items([row()])
        self.reject_row({**row(), 'itemName': LINK})

    def test_item_url_exception_requires_exact_affiliate_url(self):
        self.reject_row({**row(), 'itemUrl': LINK + '&ut=pc'})
        self.policy.check_api_items([{**row(), 'itemUrl': 'https://item.rakuten.co.jp/fixture/'}])

    def test_host_scheme_authority_and_fragment_forgery_fail(self):
        for url in (LINK.replace('https://', 'http://', 1),
                    LINK.replace('hb.afl.rakuten.co.jp', 'hb.afl.rakuten.co.jp.evil'),
                    LINK.replace('https://', 'https://user@', 1),
                    LINK.replace('.co.jp/hgc', '.co.jp:444/hgc'),
                    LINK + '#fixture', LINK + '\\fixture', LINK + '\n'):
            self.reject_row(row(url))

    def test_duplicate_query_keys_fail(self):
        self.reject_row(row(LINK + '&rafcid=other'))
        self.reject_row(row(LINK + '&%72afcid=other'))

    def test_unsupported_parameters_fail_when_public_id_is_present(self):
        self.reject_row(row(LINK + '&unrecognized=value'))

    def test_untrusted_destinations_fail(self):
        for url in (LINK.replace('item.rakuten.co.jp', 'evil.example'),
                    LINK.replace('m.rakuten.co.jp', 'evil.example'),
                    LINK.replace('https%3A%2F%2Fitem', 'http%3A%2F%2Fitem'),
                    LINK.replace('item.rakuten.co.jp', 'user%40item.rakuten.co.jp'),
                    LINK.replace('item.rakuten.co.jp', '%5Bbroken')):
            self.reject_row(row(url))

    def test_missing_destination_or_bad_query_fail(self):
        self.reject_row(row('https://hb.afl.rakuten.co.jp/hgc/public/?rafcid=wsc_i_is_' + CREDS.application_id))
        self.reject_row(row(LINK + '&broken'))

    def test_only_fully_validated_response_grants_provenance(self):
        self.reject_row({**row(), 'itemName': CREDS.access_key})
        with self.assertRaisesRegex(PipelineError, 'UNVERIFIED_TRACKING_URL'):
            self.policy.check_products(products())

    def test_partial_batch_does_not_grant_provenance(self):
        with self.assertRaises(PipelineError):
            self.policy.check_api_items([row(), {**row(), 'shopName': CREDS.access_key}])
        with self.assertRaisesRegex(PipelineError, 'UNVERIFIED_TRACKING_URL'):
            self.policy.check_products(products())

    def test_approval_is_client_local_and_exact(self):
        self.policy.check_api_items([row()])
        for policy, data in ((OutputPolicy(CREDS), products()),
                             (self.policy, products(LINK + '&ut=pc'))):
            with self.assertRaisesRegex(PipelineError, 'UNVERIFIED_TRACKING_URL'):
                policy.check_products(data)

    def test_product_metadata_and_non_url_fields_remain_strict(self):
        self.policy.check_api_items([row()])
        for secret in CREDS.values():
            for field in ('name', 'metadata'):
                data = products()
                data[field] = secret
                with self.assertRaisesRegex(PipelineError, 'SECRET_IN_OUTPUT'):
                    self.policy.check_products(data)
        data = products()
        data['categories'][0]['items'][0]['name'] = LINK
        with self.assertRaisesRegex(PipelineError, 'SECRET_IN_OUTPUT'):
            self.policy.check_products(data)

    def test_history_and_status_have_no_tracking_exception(self):
        for context in ('history', 'status'):
            with self.assertRaisesRegex(PipelineError, 'SECRET_IN_OUTPUT'):
                self.policy.reject({'value': LINK}, context)
            encoded = ''.join('%{:02X}'.format(ord(c)) for c in CREDS.access_key)
            with self.assertRaisesRegex(PipelineError, 'SECRET_IN_OUTPUT'):
                self.policy.reject({'value': encoded}, context)

    def test_token_pattern_and_redacted_diagnostics(self):
        self.reject_row({**row(), 'unknown': 'ghp_' + 'A' * 30})
        for secret in CREDS.values():
            self.assertNotIn(secret, repr(self.policy))
        error = self.reject_row({**row(), 'itemName': CREDS.application_id})
        self.assertEqual(error.diagnostic, {'credentialKind': 'application_id',
                                          'context': 'api', 'field': 'itemName'})

    def test_credential_overlap_does_not_exempt_access_key(self):
        policy = OutputPolicy(Credentials(CREDS.application_id, CREDS.application_id, CREDS.affiliate_id))
        with self.assertRaisesRegex(PipelineError, 'ACCESS_KEY'):
            policy.check_api_items([row()])

    def test_encoding_depth_is_bounded(self):
        value = ''.join('%{:02X}'.format(ord(c)) for c in CREDS.access_key)
        for _ in range(10):
            value = value.replace('%', '%25')
        self.reject_row({**row(), 'itemName': value})


class OutputPipelineTests(unittest.TestCase):
    def opener(self, payloads):
        responses = []
        for payload in payloads:
            response = Mock()
            response.status = 200
            response.read.return_value = json_bytes(payload)
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            responses.append(response)
        return Mock(open=Mock(side_effect=responses))

    def client(self, payloads):
        tick = [0.0]
        def sleep(n):
            tick[0] += n
        return RakutenClient(CREDS, 'http://proxy.example.invalid:3128', len(payloads),
                             opener=self.opener(payloads), clock=lambda: tick[0], sleep=sleep)

    def test_client_keeps_tracking_url_without_logging_it(self):
        client = self.client([{'items': [row()]}])
        result = client({'elements': ','.join(row()), 'hits': 1})
        self.assertEqual(result['items'][0]['affiliateUrl'], LINK)
        self.assertEqual(client.events, [{'request': 1, 'httpStatus': 200, 'items': 1}])
        for secret in CREDS.values():
            self.assertNotIn(secret, str(client.events))

    def test_client_failure_has_no_item_count_or_secret_diagnostics(self):
        client = self.client([{'items': [{**row(), 'itemName': CREDS.access_key}]}])
        with self.assertRaisesRegex(PipelineError, 'SECRET_IN_OUTPUT'):
            client({'elements': ','.join(row()), 'hits': 1})
        self.assertEqual(client.events, [{'request': 1, 'httpStatus': 200}])
        self.assertEqual(client.opener.open.call_count, 1)

    def test_discarded_api_fields_still_checked(self):
        client = self.client([{'items': [{**row(), 'unknown': CREDS.access_key}]}])
        with self.assertRaisesRegex(PipelineError, 'SECRET_IN_OUTPUT'):
            client({'elements': ','.join(row()), 'hits': 1})

    def test_many_qps_waits_complete_without_rounding_loop(self):
        client = self.client([{'items': [row()]}] * 16)
        starts = []
        for _ in range(16):
            client({'elements': ','.join(row()), 'hits': 1})
            starts.append(client.last)
        self.assertTrue(all(b - a >= 1.0 for a, b in zip(starts, starts[1:])))
        self.assertAlmostEqual(starts[-1] - starts[0], 15 * 1.3)

    def test_full_generation_retains_urls_history_and_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = Path(tmp)/'source', Path(tmp)/'state'
            for directory in ('config', 'data'):
                shutil.copytree(fixtures.ROOT/directory, root/directory)
            fixtures.seed_collection_history(root)
            hashes = source_hashes(root)
            before = load_json(root/'data/history.json')
            queries = load_json(root/'config/queries.json')
            payloads = []
            for category in queries:
                data = fixtures.response(category=category['id'])
                for item in data['items']:
                    item.update(affiliateUrl=LINK, itemUrl=LINK)
                payloads.extend([data, copy.deepcopy(data)])
            client = self.client(payloads)
            result = collect_generation(root, state, client, {'verified': False},
                fixtures.NOW, fixtures.SHA, CREDS.values(), output_policy=client.output_policy)
            output = load_json(Path(result['generation'])/'data/products.json')
            after = load_json(Path(result['generation'])/'data/history.json')
            self.assertEqual(len(result['status']['categories']), len(queries))
            self.assertTrue(all(c['displayed'] == 18 for c in result['status']['categories']))
            self.assertTrue(all(item['url'] == LINK for c in output['categories'] for item in c['items']))
            for code, records in before.items():
                self.assertEqual(after[code][:len(records)], records)
            self.assertEqual(source_hashes(root), hashes)
            self.assertEqual(len(client.events), 2 * len(queries))

    def test_partial_failure_creates_no_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = Path(tmp)/'source', Path(tmp)/'state'
            for directory in ('config', 'data'):
                shutil.copytree(fixtures.ROOT/directory, root/directory)
            fixtures.seed_collection_history(root)
            hashes = source_hashes(root)
            good = fixtures.response()
            for item in good['items']:
                item.update(affiliateUrl=LINK, itemUrl=LINK)
            bad = copy.deepcopy(good)
            bad['items'][0]['itemName'] = CREDS.access_key
            client = self.client([good, bad])
            with self.assertRaisesRegex(PipelineError, 'SECRET_IN_OUTPUT'):
                collect_generation(root, state, client, {'verified': False},
                    fixtures.NOW, fixtures.SHA, CREDS.values(), output_policy=client.output_policy)
            self.assertFalse(state.exists())
            self.assertEqual(source_hashes(root), hashes)
            self.assertEqual(len(client.events), 2)
