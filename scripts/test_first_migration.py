"""First-day migration and note handoff fixtures; no API or remote writes."""
from __future__ import annotations
import copy
import shutil
import tempfile
import unittest
import zipfile
from datetime import timedelta
from pathlib import Path

import test_windows_pipeline as fixtures
from check_note_bundle import validate as check_note_bundle
from build_note_article import build_article, draw_cover
from git_pipeline import GitRepository
from note_provenance import make_manifest, validate_existing
from pipeline_common import PipelineError, digest, load_json, sha256, source_hashes, write_json
from prepare_legacy_note import prepare_legacy_manifest
from publication_pipeline import prepare_publication, validate_publication, run_script, verify_bundle

WEDNESDAY = fixtures.SUNDAY - timedelta(days=4)


class FirstMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base/'source'
        for folder in ('scripts','config','data','docs','note'):
            shutil.copytree(fixtures.ROOT/folder,self.root/folder)
        # Actual product values are never changed; only a copy with fixture URLs is used.
        products = load_json(self.root/'data/products.json')
        for category in products['categories']:
            for item in category['items']:
                item['url'] = fixtures.LINK
        for item in products['deals']:
            item['url'] = fixtures.LINK
        write_json(self.root/'data/products.json',products)
        self.day(WEDNESDAY)
        weekly = {'generatedAt':'2026-10-18T06:40:00+09:00', 'weekLabel':'2026/10/12〜2026/10/18',
            'title':'検証用週次記事', 'intro':'移行試験用の架空編集です。', 'featured':[],
            'points':['保存済み記事を維持する試験'], 'closing':'試験用データです。', 'hashtags':['検証']}
        write_json(self.root/'data/note-weekly.json',weekly)
        latest = self.root/'note/latest'
        (latest/'manifest.json').unlink(missing_ok=True)
        (latest/'article.md').write_bytes(build_article(weekly,products).encode('utf-8'))
        draw_cover(latest/'article-cover.png',weekly['weekLabel'])
        with zipfile.ZipFile(latest/'note-post.zip','w') as archive:
            for name in ('article.md','article-cover.png'):
                archive.write(latest/name,name)

    def tearDown(self):
        self.temp.cleanup()

    def edit(self,name,**changes):
        path=self.root/'data'/name
        data=load_json(path);data.update(changes);write_json(path,data)

    def day(self,now):
        self.now=now
        self.edit('products.json',updatedAt=now.replace(hour=6,minute=10).isoformat())
        products_hash=digest(self.root/'data/products.json')
        write_json(self.root/'data/collection-status.json',{
            'schemaVersion':1,'sourceCommit':fixtures.SHA,
            'collectedAt':now.replace(hour=6,minute=10).isoformat(),
            'productsSha256':products_hash,'historySha256':digest(self.root/'data/history.json'),
            'queriesSha256':digest(self.root/'config/queries.json'),
            'siteConfigSha256':digest(self.root/'config/site.json'),'affiliateVerified':True,
            'affiliateVerificationExpiresAt':(now+timedelta(days=20)).isoformat()})
        self.edit('editorial.json',generatedAt=now.replace(hour=6,minute=30).isoformat(),
                  sourceProductsSha256=products_hash)

    def sunday_editorial(self):
        self.day(fixtures.SUNDAY)
        self.edit('note-weekly.json',generatedAt=fixtures.SUNDAY.replace(hour=6,minute=40).isoformat(),
            sourceProductsSha256=digest(self.root/'data/products.json'),weekLabel='2026/10/19〜2026/10/25')

    def install_legacy_manifest(self):
        write_json(self.root/'note/latest/manifest.json',make_manifest(self.root,fixtures.SHA))

    def note_hashes(self):
        return {p.name:digest(p) for p in (self.root/'note/latest').iterdir() if p.is_file()}

    def test_collection_status_is_required_before_first_publication(self):
        (self.root/'data/collection-status.json').unlink()
        with self.assertRaisesRegex(PipelineError,'INPUT_UNAVAILABLE'):
            validate_publication(self.root,self.now)

    def test_weekday_without_explicit_bootstrap_stops(self):
        with self.assertRaisesRegex(PipelineError,'NOTE_BOOTSTRAP_REQUIRED'):
            validate_publication(self.root,self.now)

    def test_weekday_preserves_fixture_legacy_bundle_with_explicit_manifest(self):
        before=self.note_hashes()
        self.install_legacy_manifest()
        decision=validate_publication(self.root,self.now)
        self.assertFalse(decision['includeNote'])
        output=self.base/'publication'
        manifest=prepare_publication(self.root,output,fixtures.SHA,self.now)
        verify_bundle(output/'bundle',self.now)
        self.assertFalse(any(name.startswith('note/') for name in manifest['sha256']))
        self.assertEqual({name:self.note_hashes()[name] for name in before},before)
        self.assertEqual(check_note_bundle(self.root/'note/latest')['status'],'ready')
        provenance=load_json(self.root/'note/latest/manifest.json')
        self.assertEqual(provenance['schema_version'],1)
        self.assertEqual(provenance['source_binding'],'legacy-preserved-unbound')
        self.assertNotIn('source_products_sha256',provenance)
        self.assertNotIn('sourceProductsSha256',load_json(self.root/'data/note-weekly.json'))

    def test_first_sunday_from_legacy_generates_new_valid_bundle(self):
        self.sunday_editorial()
        source=source_hashes(self.root)
        output=self.base/'publication'
        manifest=prepare_publication(self.root,output,fixtures.SHA,self.now)
        self.assertEqual(source_hashes(self.root),source)
        self.assertTrue(manifest['includeNote'])
        self.assertEqual(len([p for p in manifest['sha256'] if p.startswith('note/')]),6)
        bundle=output/'bundle/note/latest'
        self.assertEqual(check_note_bundle(bundle)['status'],'ready')
        note=load_json(bundle/'manifest.json')
        self.assertEqual(note['schema_version'],1)
        self.assertEqual(note['source_binding'],'products-sha256')
        self.assertEqual(note['source_products_sha256'],digest(self.root/'data/products.json'))
        self.assertEqual(set(note['files_sha256']),{'article.md','article-cover.png','note-post.zip'})

    def test_missing_daily_source_is_not_filled_automatically(self):
        data=load_json(self.root/'data/editorial.json');data.pop('sourceProductsSha256')
        write_json(self.root/'data/editorial.json',data)
        with self.assertRaisesRegex(PipelineError,'EDITORIAL_GENERATION_MISMATCH'):
            validate_publication(self.root,self.now)

    def test_missing_sunday_source_stops_even_with_legacy_manifest(self):
        self.install_legacy_manifest()
        self.sunday_editorial()
        data=load_json(self.root/'data/note-weekly.json');data.pop('sourceProductsSha256')
        write_json(self.root/'data/note-weekly.json',data)
        with self.assertRaisesRegex(PipelineError,'NOTE_SOURCE_GENERATION_MISMATCH'):
            validate_publication(self.root,self.now)

    def test_daily_before_0630_is_rejected(self):
        self.install_legacy_manifest()
        self.edit('editorial.json',generatedAt=self.now.replace(hour=6,minute=29).isoformat())
        with self.assertRaisesRegex(PipelineError,'EDITORIAL_GENERATION_MISMATCH'):
            validate_publication(self.root,self.now)

    def test_weekly_before_0640_is_rejected(self):
        self.sunday_editorial()
        self.edit('note-weekly.json',generatedAt=self.now.replace(hour=6,minute=39).isoformat())
        with self.assertRaisesRegex(PipelineError,'NOTE_SOURCE_GENERATION_MISMATCH'):
            validate_publication(self.root,self.now)

    def test_late_daily_not_available_at_publication_is_rejected(self):
        self.install_legacy_manifest()
        self.edit('editorial.json',generatedAt=self.now.replace(hour=7,minute=0).isoformat())
        with self.assertRaisesRegex(PipelineError,'EDITORIAL_GENERATION_MISMATCH'):
            validate_publication(self.root,self.now)

    def test_failed_daily_leaving_yesterday_stops(self):
        self.install_legacy_manifest()
        self.edit('editorial.json',generatedAt=(self.now-timedelta(days=1)).isoformat())
        with self.assertRaisesRegex(PipelineError,'EDITORIAL_GENERATION_MISMATCH'):
            validate_publication(self.root,self.now)

    def test_failed_weekly_leaving_prior_week_stops(self):
        self.sunday_editorial()
        self.edit('note-weekly.json',weekLabel='2026/10/12〜2026/10/18')
        with self.assertRaisesRegex(PipelineError,'NOTE_WEEK_OR_GENERATION_STALE'):
            validate_publication(self.root,self.now)

    def test_legacy_hash_is_not_inferred_from_current_prices(self):
        self.install_legacy_manifest()
        self.day(self.now+timedelta(days=1))
        self.assertFalse(validate_publication(self.root,self.now)['includeNote'])
        self.assertNotIn('source_products_sha256',load_json(self.root/'note/latest/manifest.json'))

    def test_legacy_bundle_tampering_stops(self):
        self.install_legacy_manifest()
        path=self.root/'note/latest/article.md'
        path.write_bytes(path.read_bytes()+b'\ntampered')
        with self.assertRaises(PipelineError): validate_publication(self.root,self.now)

    def test_legacy_editorial_tampering_stops(self):
        self.install_legacy_manifest()
        self.edit('note-weekly.json',closing='changed fixture')
        with self.assertRaisesRegex(PipelineError,'NOTE_EXISTING_BUNDLE_MISMATCH'):
            validate_publication(self.root,self.now)

    def test_legacy_does_not_accept_fabricated_source_hash(self):
        self.install_legacy_manifest()
        self.edit('note-weekly.json',sourceProductsSha256='f'*64)
        with self.assertRaises(PipelineError): validate_publication(self.root,self.now)

    def test_zip_must_match_the_individual_files(self):
        self.install_legacy_manifest()
        path=self.root/'note/latest/note-post.zip'
        with zipfile.ZipFile(path,'w') as archive:
            archive.writestr('article.md',b'wrong')
            archive.writestr('article-cover.png',b'wrong')
        with self.assertRaisesRegex(PipelineError,'NOTE_ZIP_CONTENT_MISMATCH'):
            validate_publication(self.root,self.now)

    def test_bootstrap_is_only_from_exact_committed_legacy_bytes(self):
        repo=GitRepository(self.root)
        repo.run('init','-b','main')
        repo.run('config','core.autocrlf','true')
        article = self.root/'note/latest/article.md'
        article.write_bytes(article.read_bytes().replace(b'\r\n',b'\n').replace(b'\n',b'\r\n'))
        files=['data/note-weekly.json','note/latest/article.md','note/latest/article-cover.png','note/latest/note-post.zip']
        repo.run('add',*files);repo.run('commit','-m','test: legacy note fixture')
        commit=repo.run('rev-parse','HEAD').decode().strip()
        before=self.note_hashes()
        output=self.base/'legacy-manifest.json'
        record=prepare_legacy_manifest(self.root,output,commit,self.now)
        self.assertEqual(record['legacy_source_commit'],commit)
        self.assertEqual(record['files_sha256']['article.md'],sha256(repo.run('show',commit+':note/latest/article.md')))
        self.assertEqual(self.note_hashes(),before)
        self.assertFalse((self.root/'note/latest/manifest.json').exists())
        self.edit('note-weekly.json',closing='changed fixture')
        with self.assertRaisesRegex(PipelineError,'SOURCE_CHANGED'):
            prepare_legacy_manifest(self.root,self.base/'second.json',commit,self.now)

    def test_bootstrap_refuses_sunday_stale_week_and_repository_output(self):
        for now,output in ((fixtures.SUNDAY,self.base/'sunday.json'),
                           (self.now+timedelta(days=7),self.base/'stale.json'),
                           (self.now,self.root/'unsafe.json')):
            with self.assertRaises(PipelineError):
                prepare_legacy_manifest(self.root,output,fixtures.SHA,now)

    def test_modern_manifest_requires_a_real_source_field(self):
        with self.assertRaisesRegex(PipelineError,'NOTE_SOURCE_GENERATION_MISMATCH'):
            make_manifest(self.root)

    def test_old_manifest_without_content_hashes_is_not_silently_trusted(self):
        write_json(self.root/'note/latest/manifest.json',{
            'generated_at':load_json(self.root/'data/note-weekly.json')['generatedAt'],
            'source_products_sha256':'a'*64})
        with self.assertRaises(PipelineError): validate_publication(self.root,self.now)
