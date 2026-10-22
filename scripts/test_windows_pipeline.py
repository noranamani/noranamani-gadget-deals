"""Offline safety tests: no external API, credentials, GitHub write or deployment."""
from __future__ import annotations
import copy
import io
import json
import shutil
import ssl
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from collection_pipeline import collect_generation, validate_queries
from fetch_rakuten import append_history
from git_pipeline import GitRepository
from pipeline_common import (COLLECTION_FILES, JST, PipelineError, SnapshotStore, digest,
    exclusive_lock, history_preserved, json_bytes, load_json, parse_json, read_bytes,
    sha256, source_hashes, validate_history, write_bytes, write_json)
from publication_pipeline import prepare_publication, validate_publication, verify_bundle, persist_note
from rakuten_client import RakutenClient, affiliate_url, local_host, validate_proxy
from run_windows_update import load_config, require_start_window, require_finish_window, run_update
from windows_credentials import Credentials, affiliate_proof, record_affiliate_proof

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 21, 2, 1, tzinfo=timezone.utc)
SUNDAY = datetime(2026, 10, 25, 6, 50, tzinfo=JST)
SHA = 'a' * 40
CREDS = Credentials('fixture-app-private', 'fixture-key-private', 'fixture-aff-private')
LINK = 'https://hb.afl.rakuten.co.jp/hgc/public-fixture/?pc=https%3A%2F%2Fexample.invalid%2Fitem'

def response(count=18, category='fixture'):
    return {'items': [dict(itemCode=category+':'+str(n), itemName='検証商品 '+str(n),
        itemPrice=1000+n, affiliateUrl=LINK, itemUrl='https://example.invalid/product',
        reviewAverage=4.2, reviewCount=100+n, postageFlag=0, pointRate=1,
        mediumImageUrls=[{'imageUrl':'https://example.invalid/image.jpg'}], shopName='検証店')
        for n in range(count)]}

class TemporarySource(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / 'source'
        for folder in ('data', 'config'):
            shutil.copytree(ROOT / folder, self.root / folder)
        self.state = self.base / 'state'
    def tearDown(self):
        self.temp.cleanup()
    def collect(self, requester=None, **kwargs):
        queries = load_json(self.root/'config/queries.json')
        payloads = [response(category=q['id']) for q in queries for _ in range(2)]
        requester = requester or Mock(side_effect=payloads)
        return collect_generation(self.root, self.state, requester,
            {'verified':False, 'expiresAt':None}, NOW, SHA, CREDS.values(), **kwargs)

class CollectionTests(TemporarySource):
    def test_full_dynamic_categories_and_history_prefixes(self):
        before, hashes = load_json(self.root/'data/history.json'), source_hashes(self.root)
        result = self.collect()
        generation = Path(result['generation'])
        after = load_json(generation/'data/history.json')
        self.assertEqual(len(result['status']['categories']), 8)
        self.assertTrue(all(c['displayed']==18 for c in result['status']['categories']))
        self.assertEqual(history_preserved(before, after)['previousRecords'], sum(map(len,before.values())))
        self.assertEqual(source_hashes(self.root), hashes)
        pointer = load_json(self.state/'CURRENT.json')
        for name in COLLECTION_FILES:
            self.assertEqual(pointer['sha256'][name], digest(generation/name))
        backups = list((self.state/'backups').glob('*/history.json'))
        self.assertEqual(digest(backups[0]), hashes['data/history.json'])
        self.assertTrue(all(i['url']==LINK for c in load_json(generation/'data/products.json')['categories'] for i in c['items']))
    def test_new_category_is_discovered_without_code_changes(self):
        q = load_json(self.root/'config/queries.json')
        q.append({**q[0], 'id':'new-category', 'name':'追加カテゴリ', 'keyword':'追加'})
        write_json(self.root/'config/queries.json', q)
        self.assertEqual(self.collect()['status']['categories'][-1]['id'], 'new-category')
    def test_missing_history_stops_before_api(self):
        (self.root/'data/history.json').unlink()
        request=Mock()
        with self.assertRaises(PipelineError): self.collect(request)
        request.assert_not_called()
        self.assertFalse((self.state/'CURRENT.json').exists())
    def test_corrupt_history_stops_before_api(self):
        write_bytes(self.root/'data/history.json', b'{broken')
        request=Mock()
        with self.assertRaises(PipelineError): self.collect(request)
        request.assert_not_called()
    def test_history_invalid_schema_stops_before_api(self):
        write_json(self.root/'data/history.json', {'x':[{'date':'2026-10-01','price':False}]})
        request=Mock()
        with self.assertRaises(PipelineError): self.collect(request)
        request.assert_not_called()
    def test_partial_category_failure_has_no_pointer(self):
        request=Mock(side_effect=[response(), response(), PipelineError('RAKUTEN_HTTP_500')])
        with self.assertRaisesRegex(PipelineError,'500'): self.collect(request)
        self.assertEqual(request.call_count,3)
        self.assertFalse(self.state.exists())
    def test_empty_category_fails(self):
        with self.assertRaisesRegex(PipelineError,'EMPTY'): self.collect(Mock(return_value={'items':[]}))
    def test_abnormally_small_category_fails(self):
        with self.assertRaisesRegex(PipelineError,'SMALL'): self.collect(Mock(return_value=response(2)))
        self.assertFalse(self.state.exists())
    def test_invalid_affiliate_link_fails(self):
        data=response(); data['items'][0]['affiliateUrl']='https://example.invalid/guessed'
        with self.assertRaisesRegex(PipelineError,'ROW_INVALID'): self.collect(Mock(return_value=data))
    def test_source_changed_before_commit_stops(self):
        old=self.collect()
        pointer=read_bytes(self.state/'CURRENT.json')
        def checkpoint(phase):
            if phase=='before_pointer':
                write_json(self.root/'config/site.json', {'changed':True})
        with self.assertRaisesRegex(PipelineError,'SOURCE_CHANGED'): self.collect(checkpoint=checkpoint)
        self.assertEqual(read_bytes(self.state/'CURRENT.json'), pointer)
    def test_interrupted_write_preserves_old_generation(self):
        self.collect()
        pointer=read_bytes(self.state/'CURRENT.json')
        for phase in ('after_products','before_pointer'):
            def interrupt(at):
                if at==phase: raise PipelineError('SIMULATED_DISK_FAILURE')
            with self.subTest(phase=phase), self.assertRaisesRegex(PipelineError,'SIMULATED'):
                self.collect(checkpoint=interrupt)
            self.assertEqual(read_bytes(self.state/'CURRENT.json'),pointer)
    def test_secret_reflection_stops_before_save(self):
        data=response(); data['items'][0]['itemName']=CREDS.access_key
        with self.assertRaisesRegex(PipelineError,'SECRET_IN_OUTPUT'): self.collect(Mock(return_value=data))
        self.assertFalse(self.state.exists())

class ValidationTests(unittest.TestCase):
    def test_duplicate_json_key(self):
        with self.assertRaisesRegex(PipelineError,'DUPLICATE'): parse_json(b'{"a":1,"a":2}')
    def test_nonfinite_json(self):
        with self.assertRaisesRegex(PipelineError,'NONFINITE'): parse_json(b'{"a":NaN}')
    def test_invalid_json(self):
        with self.assertRaises(PipelineError): parse_json(b'not-json')
    def test_history_loss_detected(self):
        with self.assertRaisesRegex(PipelineError,'LOSS'):
            history_preserved({'a':[{'date':'2026-10-01','price':10}]},{'b':[]})
    def test_history_order_rejected(self):
        with self.assertRaises(PipelineError):
            validate_history({'a':[{'date':'2026-10-02','price':1},{'date':'2026-10-01','price':1}]})
    def test_history_invalid_calendar_date(self):
        with self.assertRaises(PipelineError):
            validate_history({'a':[{'date':'2026-02-30','price':1}]})
    def test_same_day_idempotent(self):
        h={'a':[{'date':'2026-10-01','price':10}]}
        append_history('a',10,h,'2026-10-01',120)
        self.assertEqual(len(h['a']),1)
    def test_same_day_conflict(self):
        h={'a':[{'date':'2026-10-01','price':10}]}
        with self.assertRaisesRegex(PipelineError,'SAME_DAY'): append_history('a',11,h,'2026-10-01',120)
        self.assertEqual(h['a'][0]['price'],10)
    def test_history_retention_and_no_missing_day_fabrication(self):
        start=datetime(2025,1,1)
        h={'a':[{'date':(start+timedelta(days=n)).date().isoformat(),'price':10} for n in range(130)]}
        append_history('a',11,h,'2026-10-01',120)
        self.assertEqual(len(h['a']),131)
        self.assertEqual(h['a'][-2]['date'],'2025-05-10')
    def test_duplicate_query_ids(self):
        q={'id':'x','name':'x','keyword':'x'}
        with self.assertRaises(PipelineError): validate_queries([q,q])
    def test_invalid_query_price_range(self):
        with self.assertRaises(PipelineError):
            validate_queries([{'id':'x','name':'x','keyword':'x','minPrice':20,'maxPrice':10}])
    def test_lock_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            with exclusive_lock(Path(tmp)):
                with self.assertRaisesRegex(PipelineError,'ALREADY_RUNNING'):
                    with exclusive_lock(Path(tmp)): pass
            self.assertFalse((Path(tmp)/'pipeline.lock').exists())

class ClientTests(unittest.TestCase):
    def client(self, opener, budget=1, **kwargs):
        return RakutenClient(CREDS,'http://proxy.example.invalid:3128',budget,opener=opener,**kwargs)
    def params(self):
        return {'elements':','.join(response(1)['items'][0]),'keyword':'fixture','hits':3}
    def opener(self, payload):
        response_mock=Mock()
        response_mock.status=200
        response_mock.read.return_value=json_bytes(payload)
        response_mock.__enter__=Mock(return_value=response_mock)
        response_mock.__exit__=Mock(return_value=False)
        return Mock(open=Mock(return_value=response_mock))
    def test_tls_and_literal_api_url(self):
        opener=self.opener(response(1)); client=self.client(opener)
        result=client(self.params())
        self.assertEqual(result['items'][0]['affiliateUrl'],LINK)
        self.assertEqual(client.context.verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(client.context.check_hostname)
        self.assertNotIn(CREDS.access_key,str(client.events))
    def test_no_retry_401(self): self.check_http_error(401)
    def test_no_retry_403(self): self.check_http_error(403)
    def test_no_retry_429(self): self.check_http_error(429)
    def test_no_retry_500(self): self.check_http_error(500)
    def check_http_error(self,status):
        opener=Mock(open=Mock(side_effect=HTTPError('https://example.invalid/private',status,'private',{},io.BytesIO(b'private'))))
        with self.assertRaisesRegex(PipelineError,'RAKUTEN_HTTP_'+str(status)):
            self.client(opener)(self.params())
        self.assertEqual(opener.open.call_count,1)
    def test_timeout_no_retry(self):
        opener=Mock(open=Mock(side_effect=TimeoutError(CREDS.access_key)))
        with self.assertRaisesRegex(PipelineError,'NETWORK_ERROR'): self.client(opener)(self.params())
        self.assertEqual(opener.open.call_count,1)
    def test_invalid_json_stops(self):
        opener=self.opener(response(1)); opener.open.return_value.read.return_value=b'{invalid'
        with self.assertRaises(PipelineError): self.client(opener)(self.params())
    def test_budget_enforced(self):
        opener=self.opener(response(1)); client=self.client(opener)
        client(self.params())
        with self.assertRaisesRegex(PipelineError,'BUDGET'): client(self.params())
        self.assertEqual(opener.open.call_count,1)
    def test_qps_spacing(self):
        tick=[0.0]; waits=[]
        def sleep(n): waits.append(n); tick[0]+=n
        client=self.client(self.opener(response(1)),budget=2,clock=lambda:tick[0],sleep=sleep)
        client(self.params()); client(self.params())
        self.assertGreaterEqual(sum(waits),1.3)
    def test_affiliate_id_is_not_inferred_from_link(self):
        self.assertTrue(affiliate_url(LINK))
        self.assertNotIn(CREDS.affiliate_id,LINK)
    def test_untrusted_link_rejected(self):
        for url in ('http://hb.afl.rakuten.co.jp/hgc/a','https://hb.afl.rakuten.co.jp.evil/hgc/a',
                    'https://user@hb.afl.rakuten.co.jp/hgc/a','https://hb.afl.rakuten.co.jp:444/hgc/a'):
            with self.subTest(url=url): self.assertFalse(affiliate_url(url))
    def test_proxy_credentials_and_query_rejected(self):
        for url in ('https://proxy:3128','http://user:secret@proxy:3128','http://proxy:3128/?secret=1'):
            with self.subTest(url=url), self.assertRaises(PipelineError): validate_proxy(url)
    def test_proxy_bypass_local_policy(self):
        for host in ('localhost','127.0.0.1','192.168.2.12','192.168.3.99','intranet'):
            self.assertTrue(local_host(host))
        self.assertFalse(local_host('openapi.rakuten.co.jp'))
    def test_secret_in_response_rejected(self):
        data=response(1); data['items'][0]['itemName']=CREDS.application_id
        with self.assertRaisesRegex(PipelineError,'SECRET_IN_OUTPUT'):
            self.client(self.opener(data))(self.params())

class CredentialAndWindowTests(unittest.TestCase):
    def test_repr_redacted(self):
        for value in CREDS.values(): self.assertNotIn(value,repr(CREDS))
    def test_no_proof_means_publish_unverified(self):
        self.assertFalse(affiliate_proof(None,CREDS,NOW)['verified'])
    def test_account_confirmation_proof_excludes_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'proof.json'
            record_affiliate_proof(path,CREDS,CREDS.affiliate_id,NOW)
            self.assertTrue(affiliate_proof(path,CREDS,NOW)['verified'])
            for value in CREDS.values(): self.assertNotIn(value,path.read_text())
    def test_wrong_account_does_not_create_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'proof.json'
            with self.assertRaises(PipelineError): record_affiliate_proof(path,CREDS,'wrong',NOW)
            self.assertFalse(path.exists())
    def test_proof_expiry_and_credential_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'proof.json'
            record_affiliate_proof(path,CREDS,CREDS.affiliate_id,NOW)
            with self.assertRaises(PipelineError): affiliate_proof(path,CREDS,NOW+timedelta(days=30))
            with self.assertRaises(PipelineError): affiliate_proof(path,Credentials('new','key','id'),NOW)
    def test_apply_disabled_before_credentials_or_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime=Path(tmp)/'absent'
            with patch('run_windows_update.read_windows_credentials') as read:
                with self.assertRaisesRegex(PipelineError,'PRODUCTION_DISABLED'):
                    run_update({'enabled':False,'runtime_dir':runtime},True)
                read.assert_not_called()
                self.assertFalse(runtime.exists())
    def test_start_window(self):
        require_start_window(SUNDAY.replace(hour=6,minute=10))
        for hour,minute in ((6,9),(6,21),(23,0)):
            with self.subTest(hour=hour,minute=minute), self.assertRaises(PipelineError):
                require_start_window(SUNDAY.replace(hour=hour,minute=minute))
    def test_finish_window(self):
        started=SUNDAY.replace(hour=6,minute=10)
        require_finish_window(started+timedelta(minutes=14),started)
        for ended in (started+timedelta(minutes=16),started+timedelta(days=1)):
            with self.assertRaises(PipelineError): require_finish_window(ended,started)
    def test_config_refuses_unknown_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'config.json'
            write_json(path,{'enabled':False,'runtime_dir':tmp,'proxy_url':'http://proxy:3128','password':'x'})
            with self.assertRaises(PipelineError): load_config(path)

class GitTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)/'work'; self.root.mkdir()
        self.repo=GitRepository(self.root)
        self.repo.run('init','-b','main')
        write_bytes(self.root/'keep.txt',b'preserve')
        self.repo.run('add','keep.txt'); self.repo.run('commit','-m','test: initial')
        self.base=self.repo.run('rev-parse','HEAD').decode().strip()
        self.remote=Path(self.temp.name)/'remote.git'
        self.repo.run('init','--bare',str(self.remote))
        self.repo.run('remote','add','origin',str(self.remote))
        self.repo.run('push','origin','main')
    def tearDown(self): self.temp.cleanup()
    def files(self,content=b'{}'): return {name:content for name in COLLECTION_FILES}
    def test_single_commit_triple_no_checkout_mutation(self):
        commit=self.repo.prepare_commit(self.base,self.files(),'collection')
        self.assertEqual(self.repo.run('diff-tree','--no-commit-id','--name-only','-r',commit).decode().splitlines(),sorted(COLLECTION_FILES))
        self.assertEqual(self.repo.run('rev-parse','HEAD').decode().strip(),self.base)
        self.assertFalse((self.root/'data').exists())
        self.assertEqual(self.repo.run('show',commit+':keep.txt'),b'preserve')
        self.assertEqual(self.repo.push_if_unchanged(self.base,commit),commit)
    def test_push_race_preserves_other_writer(self):
        first=self.repo.prepare_commit(self.base,self.files(b'{"first":1}'),'collection')
        second=self.repo.prepare_commit(self.base,self.files(b'{"second":1}'),'collection')
        self.repo.push_if_unchanged(self.base,first)
        with self.assertRaisesRegex(PipelineError,'PUSH_CONFLICT'):
            self.repo.push_if_unchanged(self.base,second)
        self.assertEqual(self.repo.remote_tip(),first)
    def test_collection_disallowed_file_rejected(self):
        with self.assertRaises(PipelineError):
            self.repo.prepare_commit(self.base,{'data/history.json':b'{}'},'collection')
        with self.assertRaises(PipelineError):
            self.repo.prepare_commit(self.base,{**self.files(),'README.md':b'x'},'collection')
    def test_note_disallowed_path_rejected(self):
        with self.assertRaises(PipelineError):
            self.repo.prepare_commit(self.base,{'note/latest/../../../data/products.json':b'x'},'note')
    def test_archive_does_not_touch_checkout(self):
        dest=Path(self.temp.name)/'archive'
        self.repo.archive(self.base,dest)
        self.assertEqual(read_bytes(dest/'keep.txt'),b'preserve')
        self.assertEqual(self.repo.run('status','--porcelain'),b'')
    def test_archive_rejects_export_substitution_instead_of_changing_blob_bytes(self):
        write_bytes(self.root/'.gitattributes',b'keep.txt export-subst\n')
        write_bytes(self.root/'keep.txt',b'$Format:%H$\n')
        self.repo.run('add','.gitattributes','keep.txt')
        self.repo.run('commit','-m','test: export substitution fixture')
        commit=self.repo.run('rev-parse','HEAD').decode().strip()
        dest=Path(self.temp.name)/'unsafe-archive'
        with self.assertRaisesRegex(PipelineError,'ARCHIVE_CONTENT_MISMATCH'):
            self.repo.archive(commit,dest)
        self.assertFalse(dest.exists())
    def test_pre_push_deadline_failure_leaves_remote(self):
        commit=self.repo.prepare_commit(self.base,self.files(),'collection')
        def fail(): raise PipelineError('DEADLINE')
        with self.assertRaisesRegex(PipelineError,'DEADLINE'):
            self.repo.push_if_unchanged(self.base,commit,fail)
        self.assertEqual(self.repo.remote_tip(),self.base)

class PublicationTests(TemporarySource):
    def setUp(self):
        super().setUp()
        products=load_json(self.root/'data/products.json')
        products['updatedAt']=SUNDAY.replace(hour=6,minute=10).isoformat()
        write_json(self.root/'data/products.json',products)
        status={'schemaVersion':1,'sourceCommit':SHA,'collectedAt':products['updatedAt'],
            'productsSha256':digest(self.root/'data/products.json'),
            'historySha256':digest(self.root/'data/history.json'),
            'queriesSha256':digest(self.root/'config/queries.json'),
            'siteConfigSha256':digest(self.root/'config/site.json'),
            'affiliateVerified':True,'affiliateVerificationExpiresAt':(SUNDAY+timedelta(days=5)).isoformat()}
        write_json(self.root/'data/collection-status.json',status)
        daily=load_json(self.root/'data/editorial.json')
        daily.update(generatedAt=SUNDAY.replace(hour=6,minute=30).isoformat(),sourceProductsSha256=status['productsSha256'])
        write_json(self.root/'data/editorial.json',daily)
        weekly=load_json(self.root/'data/note-weekly.json')
        weekly.update(generatedAt=SUNDAY.replace(hour=6,minute=40).isoformat(),
            sourceProductsSha256=status['productsSha256'],weekLabel='2026/10/19〜2026/10/25')
        write_json(self.root/'data/note-weekly.json',weekly)
    def edit(self,name,**changes):
        path=self.root/'data'/name; value=load_json(path); value.update(changes); write_json(path,value)
    def test_matching_generation_ready(self):
        self.assertTrue(validate_publication(self.root,SUNDAY)['includeNote'])
    def test_status_hash_mismatch(self):
        self.edit('products.json',updatedAt='2026-10-24T21:11:00Z')
        with self.assertRaisesRegex(PipelineError,'GENERATION'): validate_publication(self.root,SUNDAY)
    def test_affiliate_confirmation_required(self):
        self.edit('collection-status.json',affiliateVerified=False)
        with self.assertRaisesRegex(PipelineError,'AFFILIATE'): validate_publication(self.root,SUNDAY)
    def test_affiliate_expired(self):
        self.edit('collection-status.json',affiliateVerificationExpiresAt=SUNDAY.isoformat())
        with self.assertRaisesRegex(PipelineError,'AFFILIATE'): validate_publication(self.root,SUNDAY)
    def test_stale_products(self):
        with self.assertRaisesRegex(PipelineError,'PRODUCTS_STALE'): validate_publication(self.root,SUNDAY+timedelta(hours=4))
    def test_editorial_wrong_source_hash(self):
        self.edit('editorial.json',sourceProductsSha256='b'*64)
        with self.assertRaisesRegex(PipelineError,'EDITORIAL'): validate_publication(self.root,SUNDAY)
    def test_editorial_older_than_products(self):
        self.edit('editorial.json',generatedAt=SUNDAY.replace(hour=6,minute=0).isoformat())
        with self.assertRaises(PipelineError): validate_publication(self.root,SUNDAY)
    def test_editorial_missing_new_category(self):
        value=load_json(self.root/'data/editorial.json')
        value['categoryNotes'].pop(); write_json(self.root/'data/editorial.json',value)
        with self.assertRaisesRegex(PipelineError,'COVERAGE'): validate_publication(self.root,SUNDAY)
    def test_wrong_category_top_pick(self):
        value=load_json(self.root/'data/editorial.json')
        value['categoryNotes'][0]['topPickCode']='unknown:1'; write_json(self.root/'data/editorial.json',value)
        with self.assertRaisesRegex(PipelineError,'TOP_PICK'): validate_publication(self.root,SUNDAY)
    def test_weekly_stale(self):
        self.edit('note-weekly.json',weekLabel='2026/10/12〜2026/10/18')
        with self.assertRaisesRegex(PipelineError,'STALE'): validate_publication(self.root,SUNDAY)
    def test_weekly_wrong_product_generation(self):
        self.edit('note-weekly.json',sourceProductsSha256='b'*64)
        with self.assertRaisesRegex(PipelineError,'NOTE_SOURCE'): validate_publication(self.root,SUNDAY)
    def test_full_site_and_note_build_only_in_temporary_environment(self):
        for folder in ('scripts','docs','note'): shutil.copytree(ROOT/folder,self.root/folder)
        original=source_hashes(self.root)
        output=self.base/'publication'
        manifest=prepare_publication(self.root,output,SHA,SUNDAY)
        self.assertEqual(source_hashes(self.root),original)
        self.assertEqual(verify_bundle(output/'bundle',SUNDAY),manifest)
        for q in load_json(self.root/'config/queries.json'):
            self.assertTrue((output/'bundle/docs'/('category-'+q['id']+'.html')).is_file())
        self.assertGreaterEqual(len(list((output/'bundle/docs').glob('comparison-*.html'))),5)
        png=read_bytes(output/'bundle/note/latest/article-cover.png')
        self.assertEqual(png[:8],b'\x89PNG\r\n\x1a\n')
        with zipfile.ZipFile(output/'bundle/note/latest/note-post.zip') as z:
            self.assertEqual(set(z.namelist()),{'article.md','article-cover.png'})
        self.assertEqual(len([p for p in manifest['sha256'] if p.startswith('note/')]),6)
        with self.assertRaisesRegex(PipelineError,'EXPIRED'):
            verify_bundle(output/'bundle',SUNDAY+timedelta(minutes=31))
        write_bytes(output/'bundle/docs/index.html',b'tampered')
        with self.assertRaisesRegex(PipelineError,'HASH_MISMATCH'): verify_bundle(output/'bundle',SUNDAY)
    def test_no_implicit_note_write(self):
        with self.assertRaisesRegex(PipelineError,'NOT_APPROVED'): persist_note(Mock(),self.base/'absent',SUNDAY)
    def test_weekday_preserves_previous_sunday_note_source(self):
        for folder in ('scripts','docs','note'): shutil.copytree(ROOT/folder,self.root/folder)
        generated = self.base/'sunday-bundle'
        prepare_publication(self.root,generated,SHA,SUNDAY)
        shutil.copytree(generated/'bundle/note',self.root/'note',dirs_exist_ok=True)
        sunday_hash=digest(self.root/'data/products.json')
        monday=SUNDAY+timedelta(days=1)
        self.edit('products.json',updatedAt=monday.replace(hour=6,minute=10).isoformat())
        product_hash=digest(self.root/'data/products.json')
        self.edit('collection-status.json',collectedAt=monday.replace(hour=6,minute=10).isoformat(),productsSha256=product_hash)
        self.edit('editorial.json',generatedAt=monday.replace(hour=6,minute=30).isoformat(),sourceProductsSha256=product_hash)
        self.assertEqual(load_json(self.root/'note/latest/manifest.json')['source_products_sha256'],sunday_hash)
        self.assertFalse(validate_publication(self.root,monday)['includeNote'])
        self.edit('note-weekly.json',sourceProductsSha256=product_hash)
        with self.assertRaisesRegex(PipelineError,'EXISTING_BUNDLE'):
            validate_publication(self.root,monday)
    def test_bundle_extra_file_rejected(self):
        bundle=self.base/'bundle'; write_bytes(bundle/'docs/index.html',b'page')
        write_json(bundle/'publication-manifest.json',{'schemaVersion':1,'sourceCommit':SHA,
            'preparedAt':SUNDAY.isoformat(),'includeNote':False,'sha256':{'docs/index.html':sha256(b'page')}})
        verify_bundle(bundle,SUNDAY)
        write_bytes(bundle/'unexpected.txt',b'x')
        with self.assertRaisesRegex(PipelineError,'FILES_MISMATCH'): verify_bundle(bundle,SUNDAY)

if __name__=='__main__': unittest.main()
