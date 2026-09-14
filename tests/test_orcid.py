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
    DEFAULT_CONFIG, PRIORITY_WEIGHTS, PROMOTED_ROLES, author_role, build_orcid_index,
    is_corresponding, load_watchlist, match_paper, names_match, needs_submitter,
    normalize_arxiv_id, normalize_doi, normalize_orcid, pi_score, work_identifiers,
)


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dashboard', ROOT / 'arXiv_query_automated_v0.4.0.py')
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)
A = '0000-0003-2895-6218'
B = '0000-0002-5612-3427'
C = '0000-0001-9879-7780'
PEOPLE = [{'name': 'Anna-Christina Eilers', 'orcid': A, 'priority': 'Highest', 'weight': 3},
          {'name': 'Jenny Greene', 'orcid': B, 'priority': 'High', 'weight': 2},
          {'name': 'Fabio Pacucci', 'orcid': C, 'priority': 'Medium', 'weight': 1}]


def external(kind, value, relationship='self'):
    return {'external-id-type': kind, 'external-id-value': value, 'external-id-relationship': relationship}


def paper(**overrides):
    """A four-author paper; overrides place the tracked PIs on it."""
    record = {'id': 'https://arxiv.org/abs/2401.12345v2', 'doi': '10.1234/test', 'title': 'A Title',
              'abstract': 'Quasar growth.', 'comments': '10 pages', 'published': '2026-09-12',
              'authors': 'Rob Roe, Mia Moe, Ann Ang, Bo Bao', 'author_count': 4,
              'author_orcids': [], 'orcid_positions': {}}
    record.update(overrides)
    record['author_orcids'] = record.get('author_orcids') or sorted(record['orcid_positions'])
    return record


def roles(matches):
    return {match['orcid']: (match['role'], match['promoted']) for match in matches}


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
        people = [PEOPLE[0], PEOPLE[1], dict(PEOPLE[2], priority='Skip (sim/theory)', weight=0)]
        record = paper(authors='Anna-Christina Eilers, Jenny Greene', author_count=2,
                       orcid_positions={A: [0]}, author_orcids=[A, C])
        matches = match_paper(record, people, {'arxiv:2401.12345': [A, B], 'doi:10.1234/test': [B]})
        self.assertEqual([m['orcid'] for m in matches], [A, B])
        self.assertEqual(pi_score(matches), 5)
        # Names never identify a PI, they only place one the ORCID evidence already found.
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

    def test_unusable_email_is_dropped_from_the_watchlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'pis.json'
            config.write_text(json.dumps({'people': [
                {'name': 'Kept', 'orcid': A, 'priority': 'Medium', 'email': 'Anna.Eilers@MIT.edu'},
                {'name': 'Dropped', 'orcid': B, 'priority': 'Medium', 'email': 'not an address'}]}))
            people = {p['name']: p for p in load_watchlist(config)}
            self.assertEqual(people['Kept']['email'], 'anna.eilers@mit.edu')
            self.assertNotIn('email', people['Dropped'])

    def _tracker(self, path, header_row, data_row):
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Outreach Tracker" r:id="rId1"/></sheets></workbook>')
            z.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
            z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                       + header_row + data_row + '</sheetData></worksheet>')

    def test_tracker_import_splits_people_and_updates_priorities(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'tracker.xlsx'
            self._tracker(path,
                '<row r="3"><c r="A3" t="inlineStr"><is><t>Priority</t></is></c><c r="E3" t="inlineStr"><is><t>PI (contact)</t></is></c></row>',
                '<row r="4"><c r="A4" t="inlineStr"><is><t>Medium</t></is></c><c r="E4" t="inlineStr"><is><t>Anna-Christina Eilers / Jenny Greene</t></is></c></row>')
            people = load_watchlist(tracker_path=path)
            self.assertEqual([p['orcid'] for p in people], [A, B])
            self.assertEqual([p['weight'] for p in people], [1, 1])

    def test_tracker_import_reads_an_optional_email_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'tracker.xlsx'
            self._tracker(path,
                '<row r="3"><c r="A3" t="inlineStr"><is><t>Priority</t></is></c><c r="E3" t="inlineStr"><is><t>PI (contact)</t></is></c>'
                '<c r="F3" t="inlineStr"><is><t>Email</t></is></c></row>',
                '<row r="4"><c r="A4" t="inlineStr"><is><t>High</t></is></c><c r="E4" t="inlineStr"><is><t>Jenny Greene</t></is></c>'
                '<c r="F4" t="inlineStr"><is><t>JGreene@princeton.edu</t></is></c></row>')
            people = load_watchlist(tracker_path=path)
            self.assertEqual([(p['orcid'], p['email']) for p in people], [(B, 'jgreene@princeton.edu')])

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
            record = {'id': 'https://arxiv.org/abs/2401.12345', 'title': dangerous, 'abstract': dangerous,
                      'authors': dangerous, 'published': '2026-09-14'}
            dashboard.generate_single_html([record])
            html = Path(dashboard.HTML_FILE).read_text(encoding='utf-8')
            self.assertNotIn(dangerous, html)
            self.assertIn('Keywords (primary ranking)', html)

    def test_generated_javascript_ranking_and_rendering(self):
        node = os.getenv('ARXIV_TEST_NODE') or shutil.which('node')
        if not node:
            self.skipTest('Node.js is needed for the generated JavaScript test')
        with tempfile.TemporaryDirectory() as tmp:
            dashboard.HTML_FILE = str(Path(tmp) / 'dashboard.html')
            # Pacucci carries no weight here, so the JS test can assert Skip entries are filtered.
            people = [PEOPLE[0], PEOPLE[1], dict(PEOPLE[2], priority='Skip (sim/theory)', weight=0)]
            dashboard.generate_single_html([], people, {'arxiv:2401.12345': [A, B], 'doi:10.1234/test': [B]})
            result = subprocess.run([node, str(ROOT / 'tests/test_dashboard.js'), dashboard.HTML_FILE],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cache_migration_retains_28_days_and_refreshes_metadata(self):
        today = datetime.now(timezone.utc).date()
        with tempfile.TemporaryDirectory() as tmp:
            dashboard.BASE_DIR = tmp
            dashboard.CACHE_FILE = str(Path(tmp) / 'arxiv_cache.json')
            retained = {'id': 'retained', 'published': str(today - timedelta(days=27))}
            expired = {'id': 'expired', 'published': str(today - timedelta(days=28))}
            Path(dashboard.CACHE_FILE).write_text(json.dumps([retained, expired]))
            upgraded = {**retained, 'doi': '10.1234/test', 'author_orcids': [], 'orcid_positions': {}}
            with patch.object(dashboard, 'fetch_oai_pmh_papers', return_value=([upgraded], True)) as fetch:
                papers = dashboard.fetch_and_cache_papers()
            self.assertEqual(papers, [upgraded])
            fetch.assert_called_once_with(str(today - timedelta(days=27)), with_status=True)
            self.assertEqual(json.loads((Path(tmp) / 'cache_metadata.json').read_text())['coverage_from'], retained['published'])

    def test_cache_without_author_positions_is_reharvested_in_full(self):
        today = datetime.now(timezone.utc).date()
        with tempfile.TemporaryDirectory() as tmp:
            dashboard.BASE_DIR = tmp
            dashboard.CACHE_FILE = str(Path(tmp) / 'arxiv_cache.json')
            Path(tmp, 'cache_metadata.json').write_text(json.dumps({'coverage_from': str(today - timedelta(days=60))}))
            recent = {'id': 'recent', 'published': str(today - timedelta(days=5)), 'doi': '', 'author_orcids': []}
            Path(dashboard.CACHE_FILE).write_text(json.dumps([recent]))
            with patch.object(dashboard, 'fetch_oai_pmh_papers', return_value=([], True)) as fetch:
                dashboard.fetch_and_cache_papers()
            fetch.assert_called_once_with(str(today - timedelta(days=27)), with_status=True)
            # Once positions are recorded, only the tail of the window is re-queried.
            Path(dashboard.CACHE_FILE).write_text(json.dumps([{**recent, 'orcid_positions': {}}]))
            with patch.object(dashboard, 'fetch_oai_pmh_papers', return_value=([], True)) as fetch:
                dashboard.fetch_and_cache_papers()
            fetch.assert_called_once_with(str(today - timedelta(days=5)), with_status=True)

    def test_reharvest_keeps_a_resolved_submitter(self):
        today = str(datetime.now(timezone.utc).date())
        with tempfile.TemporaryDirectory() as tmp:
            dashboard.BASE_DIR = tmp
            dashboard.CACHE_FILE = str(Path(tmp) / 'arxiv_cache.json')
            stored = {'id': 'paper', 'published': today, 'doi': '', 'author_orcids': [],
                      'orcid_positions': {}, 'submitter': 'Jenny Greene'}
            Path(dashboard.CACHE_FILE).write_text(json.dumps([stored]))
            fresh = {key: value for key, value in stored.items() if key != 'submitter'}
            with patch.object(dashboard, 'fetch_oai_pmh_papers', return_value=([fresh], True)):
                papers = dashboard.fetch_and_cache_papers()
            self.assertEqual(papers[0]['submitter'], 'Jenny Greene')

    def test_saved_cache_omits_recomputed_rankings(self):
        with tempfile.TemporaryDirectory() as tmp:
            dashboard.CACHE_FILE = str(Path(tmp) / 'arxiv_cache.json')
            dashboard.save_cache([{'id': 'paper', 'published': '2026-09-12', 'submitter': 'Jenny Greene',
                                   'orcid_matches': [{'name': 'Jenny Greene'}], 'pi_score': 2}])
            stored = json.loads(Path(dashboard.CACHE_FILE).read_text())[0]
            self.assertEqual(set(stored), {'id', 'published', 'submitter'})

    def test_partial_harvest_is_retried_from_original_cutoff(self):
        today = datetime.now(timezone.utc).date()
        with tempfile.TemporaryDirectory() as tmp:
            dashboard.BASE_DIR = tmp
            dashboard.CACHE_FILE = str(Path(tmp) / 'arxiv_cache.json')
            partial = {'id': 'latest', 'published': str(today), 'doi': '', 'author_orcids': [], 'orcid_positions': {}}
            with patch.object(dashboard, 'fetch_oai_pmh_papers', return_value=([partial], False)):
                dashboard.fetch_and_cache_papers()
            metadata = json.loads((Path(tmp) / 'cache_metadata.json').read_text())
            self.assertNotIn('coverage_from', metadata)
            with patch.object(dashboard, 'fetch_oai_pmh_papers', return_value=([], True)) as fetch:
                dashboard.fetch_and_cache_papers()
            fetch.assert_called_once_with(str(today - timedelta(days=27)), with_status=True)

    def test_offline_cli_does_not_create_launcher_or_fetch(self):
        tracked = next(person for person in load_watchlist() if person['weight'] > 0)
        with tempfile.TemporaryDirectory() as tmp:
            # A buried match would trigger a submitter lookup were --offline not honoured.
            Path(tmp, 'arxiv_cache.json').write_text(json.dumps([
                {'id': 'https://arxiv.org/abs/2401.00001', 'title': 'Buried', 'abstract': 'text',
                 'authors': 'Rob Roe, Mia Moe, ' + tracked['name'] + ', Ann Ang', 'author_count': 4,
                 'published': str(datetime.now(timezone.utc).date()), 'doi': '', 'comments': '',
                 'author_orcids': [tracked['orcid']], 'orcid_positions': {tracked['orcid']: [2]}}]))
            result = subprocess.run([sys.executable, str(ROOT / 'arXiv_query_automated_v0.4.0.py'),
                                     '--dir', tmp, '--offline', '--cache-days', '28'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            html = Path(tmp, 'arxiv_homepage.html').read_text(encoding='utf-8')
            self.assertIn('const defaultCacheDays = 28', html)
            self.assertIn('"role": "co-author"', html)
            self.assertIn('"promoted": false', html)
            self.assertFalse(Path(tmp, 'orcid_works_cache.json').exists())
            self.assertNotIn('submitter', json.loads(Path(tmp, 'arxiv_cache.json').read_text())[0])


class PromotionRuleTests(unittest.TestCase):
    """Only a first, second or corresponding author promotes a paper."""

    def test_first_and_second_author_promote(self):
        record = paper(authors='Anna-Christina Eilers, Jenny Greene, Ann Ang, Bo Bao',
                       orcid_positions={A: [0], B: [1]})
        matches = match_paper(record, PEOPLE, {})
        self.assertEqual(roles(matches), {A: ('first', True), B: ('second', True)})
        self.assertEqual(pi_score(matches), 5)

    def test_buried_coauthor_is_listed_but_not_promoted(self):
        record = paper(authors='Rob Roe, Mia Moe, Fabio Pacucci, Ann Ang', orcid_positions={C: [2]})
        matches = match_paper(record, PEOPLE, {})
        self.assertEqual(roles(matches), {C: ('co-author', False)})
        self.assertEqual(pi_score(matches), 0)
        self.assertEqual(len(matches), 1, 'the match must stay visible on the paper')

    def test_last_author_is_labelled_but_not_promoted(self):
        record = paper(authors='Rob Roe, Mia Moe, Ann Ang, Fabio Pacucci', orcid_positions={C: [3]})
        self.assertEqual(roles(match_paper(record, PEOPLE, {})), {C: ('last', False)})
        self.assertNotIn('last', PROMOTED_ROLES)

    def test_submitter_promotes_a_middle_author(self):
        record = paper(authors='Rob Roe, Mia Moe, Jenny Greene, Ann Ang', orcid_positions={B: [2]})
        self.assertEqual(roles(match_paper(record, PEOPLE, {})), {B: ('co-author', False)})
        record['submitter'] = 'Jennifer E. Greene'
        matches = match_paper(record, PEOPLE, {})
        self.assertEqual(roles(matches), {B: ('corresponding', True)})
        self.assertEqual(pi_score(matches), 2)

    def test_printed_contact_address_promotes(self):
        record = paper(authors='Rob Roe, Mia Moe, Fabio Pacucci, Ann Ang', orcid_positions={C: [2]},
                       comments='8 pages. Contact: fpacucci@cfa.harvard.edu')
        self.assertEqual(roles(match_paper(record, PEOPLE, {})), {C: ('corresponding', True)})

    def test_someone_elses_address_does_not_promote(self):
        record = paper(authors='Rob Roe, Mia Moe, Fabio Pacucci, Ann Ang', orcid_positions={C: [2]},
                       comments='Contact: rroe@example.edu')
        self.assertEqual(roles(match_paper(record, PEOPLE, {})), {C: ('co-author', False)})

    def test_configured_email_matches_exactly(self):
        person = dict(PEOPLE[2], email='f.pacucci@cfa.harvard.edu')
        self.assertTrue(is_corresponding(paper(abstract='Write to f.pacucci@cfa.harvard.edu.'), person))
        self.assertFalse(is_corresponding(paper(), person))

    def test_orcid_works_match_is_placed_by_name(self):
        # arXiv carried no author ORCIDs; the works index identifies the PI, the name places them.
        record = paper(authors='A. Eilers, Rob Roe, Mia Moe, Ann Ang')
        self.assertEqual(roles(match_paper(record, PEOPLE, {'arxiv:2401.12345': [A]})), {A: ('first', True)})

    def test_works_match_with_unfindable_name_is_not_promoted(self):
        record = paper(authors='Rob Roe, Mia Moe, Ann Ang', author_count=3)
        matches = match_paper(record, PEOPLE, {'doi:10.1234/test': [A]})
        self.assertEqual(roles(matches), {A: ('unknown', False)})
        self.assertEqual(pi_score(matches), 0)

    def test_untrustworthy_author_string_blocks_name_placement(self):
        # A comma inside a name makes the split disagree with the recorded author count.
        record = paper(authors='Roe, Jr., Anna-Christina Eilers', author_count=2)
        self.assertEqual(roles(match_paper(record, PEOPLE, {'arxiv:2401.12345': [A]})), {A: ('unknown', False)})

    def test_name_matching_tolerates_initials_accents_and_lone_surnames(self):
        self.assertTrue(names_match('Zoltán Haiman', 'Z. Haiman'))
        self.assertTrue(names_match('Ryan C. Hickox', 'Ryan Hickox'))
        self.assertTrue(names_match('Eilers', 'Anna-Christina Eilers'))
        self.assertFalse(names_match('Ryan C. Hickox', 'Ada Hickox'))
        self.assertFalse(names_match('Jenny Greene', 'Jenny Green'))

    def test_promoted_match_sorts_ahead_of_a_heavier_unpromoted_one(self):
        record = paper(authors='Fabio Pacucci, Rob Roe, Anna-Christina Eilers, Ann Ang',
                       orcid_positions={C: [0], A: [2]})
        matches = match_paper(record, PEOPLE, {})
        self.assertEqual([m['orcid'] for m in matches], [C, A])
        self.assertEqual(pi_score(matches), 1)

    def test_needs_submitter_only_while_a_match_is_unpromoted(self):
        record = paper(authors='Rob Roe, Mia Moe, Ann Ang, Fabio Pacucci', orcid_positions={C: [3]})
        record['orcid_matches'] = match_paper(record, PEOPLE, {})
        self.assertTrue(needs_submitter(record))
        record['submitter'] = ''
        self.assertFalse(needs_submitter(record), 'a checked paper must not be looked up again')
        promoted = paper(authors='Anna-Christina Eilers, Rob Roe, Mia Moe, Ann Ang', orcid_positions={A: [0]})
        promoted['orcid_matches'] = match_paper(promoted, PEOPLE, {})
        self.assertFalse(needs_submitter(promoted))

    def test_role_helper_survives_missing_metadata(self):
        self.assertEqual(author_role({}, PEOPLE[0], None), ('unknown', None))


OAI_RECORDS = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <ListRecords>
    <record>
      <header><identifier>oai:arXiv.org:2401.12345</identifier><datestamp>2026-09-10</datestamp></header>
      <metadata>
        <arXiv xmlns="http://arxiv.org/OAI/arXiv/">
          <id>2401.12345</id>
          <categories>astro-ph.GA astro-ph.CO</categories>
          <title>A Title</title>
          <abstract>An abstract.</abstract>
          <comments>10 pages. Corresponding author: greene@princeton.edu</comments>
          <doi>10.1234/test</doi>
          <authors>
            <author><keyname>Eilers</keyname><forenames>Anna-Christina</forenames>
              <ORCID>https://orcid.org/{A}</ORCID></author>
            <author><keyname>Roe</keyname><forenames>Rob</forenames></author>
            <author><keyname>Greene</keyname><forenames>Jenny</forenames><ORCID>{B}</ORCID></author>
          </authors>
        </arXiv>
      </metadata>
    </record>
  </ListRecords>
</OAI-PMH>
""".format(A=A, B=B)

GET_RECORD = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <GetRecord><record><metadata>
    <arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/">
      <id>2401.12345</id><submitter>Jennifer E. Greene</submitter>
    </arXivRaw>
  </metadata></record></GetRecord>
</OAI-PMH>
"""


class HarvestTests(unittest.TestCase):
    def test_author_positions_and_comments_are_harvested(self):
        with patch.object(dashboard.urllib.request, 'urlopen',
                          return_value=io.BytesIO(OAI_RECORDS.encode('utf-8'))):
            papers = dashboard.fetch_oai_pmh_papers('2026-09-01')
        self.assertEqual(len(papers), 1)
        record = papers[0]
        self.assertEqual(record['authors'], 'Anna-Christina Eilers, Rob Roe, Jenny Greene')
        self.assertEqual(record['author_count'], 3)
        self.assertEqual(record['orcid_positions'], {A: [0], B: [2]})
        self.assertEqual(record['author_orcids'], sorted([A, B]))
        self.assertIn('greene@princeton.edu', record['comments'])
        matches = match_paper(record, PEOPLE, {})
        self.assertEqual(roles(matches), {A: ('first', True), B: ('corresponding', True)})
        self.assertEqual(pi_score(matches), 5)


class SubmitterLookupTests(unittest.TestCase):
    def test_lookup_fills_submitters_and_respects_the_cap(self):
        papers = [paper(id='https://arxiv.org/abs/2401.1234%d' % n) for n in range(3)]
        with patch.object(dashboard.time, 'sleep'), \
             patch.object(dashboard.urllib.request, 'urlopen',
                          side_effect=lambda *a, **k: io.BytesIO(GET_RECORD.encode('utf-8'))):
            resolved = dashboard.fetch_submitters(papers, limit=2)
        self.assertEqual(resolved, 2)
        self.assertEqual([p.get('submitter') for p in papers],
                         ['Jennifer E. Greene', 'Jennifer E. Greene', None])

    def test_network_failure_leaves_papers_for_a_later_run(self):
        papers = [paper(id='https://arxiv.org/abs/2401.12341')]
        with patch.object(dashboard.time, 'sleep'), \
             patch.object(dashboard.urllib.request, 'urlopen',
                          side_effect=dashboard.urllib.error.URLError('offline')):
            self.assertEqual(dashboard.fetch_submitters(papers), 0)
        self.assertNotIn('submitter', papers[0])

    def test_rate_limiting_retries_then_stops_without_marking(self):
        papers = [paper(id='https://arxiv.org/abs/2401.12341')]
        error = dashboard.urllib.error.HTTPError('u', 503, 'Busy', {'Retry-After': '1'}, None)
        with patch.object(dashboard.time, 'sleep') as sleep, \
             patch.object(dashboard.urllib.request, 'urlopen', side_effect=error) as request:
            dashboard.fetch_submitters(papers)
        self.assertEqual(request.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertNotIn('submitter', papers[0])

    def test_missing_record_is_marked_as_checked(self):
        papers = [paper(id='https://arxiv.org/abs/2401.12341')]
        error = dashboard.urllib.error.HTTPError('u', 404, 'Not Found', {}, None)
        with patch.object(dashboard.time, 'sleep'), \
             patch.object(dashboard.urllib.request, 'urlopen', side_effect=error):
            dashboard.fetch_submitters(papers)
        self.assertEqual(papers[0]['submitter'], '')


if __name__ == '__main__':
    unittest.main()
