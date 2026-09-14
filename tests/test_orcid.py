import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error
import zipfile
from datetime import datetime, timedelta, timezone

from arxiv_orcid import (
    DEFAULT_CONFIG, PRIORITY_WEIGHTS, build_orcid_index, load_watchlist,
    match_paper, normalize_arxiv_id, normalize_doi, normalize_orcid, work_identifiers,
)


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dashboard', ROOT / 'arXiv_query_automated_v0.3.2.py')
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)
A = '0000-0003-2895-6218'
B = '0000-0002-5612-3427'
C = '0000-0001-9879-7780'


def external(kind, value, relationship='self'):
    return {'external-id-type': kind, 'external-id-value': value, 'external-id-relationship': relationship}


class OrcidTests(unittest.TestCase):
    def setUp(self):
        self.paths = (dashboard.BASE_DIR, dashboard.CACHE_FILE, dashboard.HTML_FILE)

    def tearDown(self):
        dashboard.BASE_DIR, dashboard.CACHE_FILE, dashboard.HTML_FILE = self.paths

    def test_identifier_normalization_and_checksum(self):
        self.assertEqual(normalize_orcid('https://orcid.org/' + A), A)
        self.assertEqual(normalize_orcid('0000-0003-2895-6219'), '')
        self.assertEqual(normalize_orcid('https://evil.org/' + A), '')
        self.assertEqual(normalize_orcid('0000-0003-4700-663x'), '0000-0003-4700-663X')
        self.assertEqual(normalize_arxiv_id('https://arxiv.org/pdf/2401.12345v3.pdf'), '2401.12345')
        self.assertEqual(normalize_arxiv_id('arXiv:astro-ph/0601001v2'), 'astro-ph/0601001')
        self.assertEqual(normalize_arxiv_id('https://elsewhere.test/2401.12345'), '')
        self.assertEqual(normalize_doi('https://doi.org/10.3847/ABC'), '10.3847/abc')

    def test_work_identifiers_exclude_container_and_include_versions(self):
        payload = {'group': [{'external-ids': {'external-id': [external('doi', '10.1/not-a-doi'),
            external('doi', '10.1234/journal', 'part-of'), external('arxiv', '2401.12345v2', 'version-of')]},
            'work-summary': [{'external-ids': {'external-id': [external('doi', '10.48550/arXiv.2402.12345'),
                external('doi', '10.1234/paper')]}}, {'url': {'value': 'https://arxiv.org/abs/astro-ph/0601001v2'}}]}]}
        self.assertEqual(work_identifiers(payload), ['arxiv:2401.12345', 'arxiv:2402.12345', 'arxiv:astro-ph/0601001',
                                                   'doi:10.1234/paper', 'doi:10.48550/arxiv.2402.12345'])

    def test_exact_matching_weights_and_no_name_fallback(self):
        people = [{'name': 'Eilers', 'orcid': A, 'priority': 'Highest', 'weight': 3},
                  {'name': 'Greene', 'orcid': B, 'priority': 'High', 'weight': 2},
                  {'name': 'Pacucci', 'orcid': C, 'priority': 'Skip (sim/theory)', 'weight': 0}]
        paper = {'id': 'https://arxiv.org/abs/2401.12345v2', 'doi': '10.1234/test', 'author_orcids': [A, C]}
        matches = match_paper(paper, people, {'arxiv:2401.12345': [A, B], 'doi:10.1234/test': [B]})
        self.assertEqual([m['orcid'] for m in matches], [A, B])
        self.assertEqual(sum(m['weight'] for m in matches), 5)
        self.assertEqual(match_paper({'authors': 'Eilers, Greene'}, people, {}), [])

    def test_default_watchlist_is_complete_and_priority_consistent(self):
        people = load_watchlist()
        self.assertEqual(len(people), 145)
        self.assertEqual(sum(p['weight'] > 0 for p in people), 121)
        self.assertTrue(all(p['orcid'] for p in people))
        self.assertTrue(all(p['weight'] == PRIORITY_WEIGHTS[p['priority']] for p in people))
        self.assertEqual(len({p['orcid'] for p in people}), 145)

    def test_duplicate_orcid_counts_once_with_highest_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'pis.json'
            config.write_text(json.dumps({'people': [{'name': 'PI', 'orcid': A, 'priority': 'Medium'},
                                                     {'name': 'PI alias', 'orcid': A, 'priority': 'Highest'}]}))
            people = load_watchlist(config)
            self.assertEqual(len(people), 1)
            self.assertEqual(people[0]['weight'], 3)

    def test_tracker_import_splits_people_and_updates_priorities(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'tracker.xlsx'
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Outreach Tracker" r:id="rId1"/></sheets></workbook>')
                z.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
                z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                    '<row r="3"><c r="A3" t="inlineStr"><is><t>Priority</t></is></c><c r="E3" t="inlineStr"><is><t>PI (contact)</t></is></c></row>'
                    '<row r="4"><c r="A4" t="inlineStr"><is><t>Medium</t></is></c><c r="E4" t="inlineStr"><is><t>Anna-Christina Eilers / Jenny Greene</t></is></c></row>'
                    '</sheetData></worksheet>')
            people = load_watchlist(tracker_path=path)
            self.assertEqual([p['orcid'] for p in people], [A, B])
            self.assertEqual([p['weight'] for p in people], [1, 1])

    def test_cache_fallback_and_skip_avoid_network(self):
        people = [{'name': 'Eilers', 'orcid': A, 'weight': 3}, {'name': 'Skip', 'orcid': C, 'weight': 0}]
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / 'works.json'
            old = {'fetched_at': 1, 'keys': ['arxiv:2401.12345']}
            cache.write_text(json.dumps({A: old}))
            with patch('urllib.request.urlopen', side_effect=urllib.error.URLError('offline')) as request:
                index, status = build_orcid_index(people, cache)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(index['arxiv:2401.12345'], [A])
            self.assertEqual(status[A]['status'], 'stale')
            self.assertEqual(json.loads(cache.read_text())[A], old)
            with patch('urllib.request.urlopen') as request:
                build_orcid_index(people, cache, refresh=True, offline=True)
                request.assert_not_called()

    def test_refresh_and_cache_reuse(self):
        people = [{'name': 'Eilers', 'orcid': A, 'weight': 3}]
        payload = {'group': [{'external-ids': {'external-id': [external('arxiv', '2401.12345')]}}]}
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / 'works.json'
            with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(payload).encode())), patch('time.sleep'):
                index, status = build_orcid_index(people, cache)
            self.assertEqual(status[A]['status'], 'current')
            with patch('urllib.request.urlopen') as request:
                again, status = build_orcid_index(people, cache)
                request.assert_not_called()
            self.assertEqual(again, index)
            self.assertEqual(status[A]['status'], 'cached')

    def test_html_script_data_cannot_close_script_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            dashboard.HTML_FILE = str(Path(tmp) / 'dashboard.html')
            dangerous = '</script><script>alert(1)</script>'
            paper = {'id': 'https://arxiv.org/abs/2401.12345', 'title': dangerous, 'abstract': dangerous,
                     'authors': dangerous, 'published': '2026-09-14'}
            dashboard.generate_single_html([paper])
            html = Path(dashboard.HTML_FILE).read_text(encoding='utf-8')
            self.assertNotIn(dangerous, html)
            self.assertIn('Keywords (primary ranking)', html)

    def test_generated_javascript_ranking_and_rendering(self):
        node = os.getenv('ARXIV_TEST_NODE') or shutil.which('node')
        if not node:
            self.skipTest('Node.js is needed for the generated JavaScript test')
        with tempfile.TemporaryDirectory() as tmp:
            dashboard.HTML_FILE = str(Path(tmp) / 'dashboard.html')
            people = [{'name': 'Eilers', 'orcid': A, 'priority': 'Highest', 'weight': 3},
                      {'name': 'Greene', 'orcid': B, 'priority': 'High', 'weight': 2},
                      {'name': 'Pacucci', 'orcid': C, 'priority': 'Skip (sim/theory)', 'weight': 0}]
            dashboard.generate_single_html([], people, {'arxiv:2401.12345': [A, B], 'doi:10.1234/test': [B]})
            result = subprocess.run([node, str(ROOT / 'tests/test_dashboard.js'), dashboard.HTML_FILE], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cache_migration_retains_28_days_and_refreshes_metadata(self):
        today = datetime.now(timezone.utc).date()
        with tempfile.TemporaryDirectory() as tmp:
            dashboard.BASE_DIR = tmp
            dashboard.CACHE_FILE = str(Path(tmp) / 'arxiv_cache.json')
            retained = {'id': 'retained', 'published': str(today - timedelta(days=27))}
            expired = {'id': 'expired', 'published': str(today - timedelta(days=28))}
            Path(dashboard.CACHE_FILE).write_text(json.dumps([retained, expired]))
            upgraded = {**retained, 'doi': '10.1234/test', 'author_orcids': []}
            with patch.object(dashboard, 'fetch_oai_pmh_papers', return_value=([upgraded], True)) as fetch:
                papers = dashboard.fetch_and_cache_papers()
            self.assertEqual(papers, [upgraded])
            fetch.assert_called_once_with(str(today - timedelta(days=27)), with_status=True)
            self.assertEqual(json.loads((Path(tmp) / 'cache_metadata.json').read_text())['coverage_from'], retained['published'])

    def test_partial_harvest_is_retried_from_original_cutoff(self):
        today = datetime.now(timezone.utc).date()
        with tempfile.TemporaryDirectory() as tmp:
            dashboard.BASE_DIR = tmp
            dashboard.CACHE_FILE = str(Path(tmp) / 'arxiv_cache.json')
            partial = {'id': 'latest', 'published': str(today), 'doi': '', 'author_orcids': []}
            with patch.object(dashboard, 'fetch_oai_pmh_papers', return_value=([partial], False)):
                dashboard.fetch_and_cache_papers()
            metadata = json.loads((Path(tmp) / 'cache_metadata.json').read_text())
            self.assertNotIn('coverage_from', metadata)
            with patch.object(dashboard, 'fetch_oai_pmh_papers', return_value=([], True)) as fetch:
                dashboard.fetch_and_cache_papers()
            fetch.assert_called_once_with(str(today - timedelta(days=27)), with_status=True)

    def test_offline_cli_does_not_create_launcher_or_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'arxiv_cache.json').write_text('[]')
            result = subprocess.run([sys.executable, str(ROOT / 'arXiv_query_automated_v0.3.2.py'),
                                     '--dir', tmp, '--offline', '--cache-days', '28'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            html = Path(tmp, 'arxiv_homepage.html').read_text(encoding='utf-8')
            self.assertIn('const defaultCacheDays = 28', html)
            self.assertFalse(Path(tmp, 'orcid_works_cache.json').exists())


if __name__ == '__main__':
    unittest.main()
