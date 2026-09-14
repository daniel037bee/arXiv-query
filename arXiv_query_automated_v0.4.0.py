from html import escape
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
import argparse
import sys
import stat
from arxiv_orcid import (
    PRIORITY_WEIGHTS, load_watchlist, build_orcid_index, match_paper,
    normalize_orcid,
)

def create_launcher_shortcut(base_dir):
    """Automatically creates a .bat, .command, or .sh file to launch the script in the future."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    script_name = os.path.basename(__file__)
    
    # 1. WINDOWS
    if os.name == 'nt':  
        bat_path = os.path.join(script_dir, 'Run_arXiv_Query.bat')
        if not os.path.exists(bat_path):
            print(f"Creating Windows launcher at: {bat_path}")
            with open(bat_path, 'w') as f:
                f.write("@echo off\n")
                f.write("echo Running arXiv Query Script...\n")
                f.write('cd /d "%~dp0"\n')
                f.write(f'"{sys.executable}" "{script_name}" --dir "{base_dir}"\n')
                f.write("pause\n")
                
    # 2. macOS
    elif sys.platform == 'darwin':  
        mac_path = os.path.join(script_dir, 'Run_arXiv_Query.command')
        if not os.path.exists(mac_path):
            print(f"Creating macOS launcher at: {mac_path}")
            with open(mac_path, 'w') as f:
                f.write("#!/bin/bash\n")
                f.write('echo "Running arXiv Query Script..."\n')
                # Securely get directory whether run as a script or double-clicked
                f.write('cd "$(dirname "${BASH_SOURCE[0]:-$0}")"\n')
                f.write(f'"{sys.executable}" "{script_name}" --dir "{base_dir}"\n')
            
            # Make the .command file executable
            st = os.stat(mac_path)
            os.chmod(mac_path, st.st_mode | stat.S_IEXEC)
            
    # 3. LINUX
    else:  
        sh_path = os.path.join(script_dir, 'run_arxiv_query.sh')
        if not os.path.exists(sh_path):
            print(f"Creating Linux launcher at: {sh_path}")
            with open(sh_path, 'w') as f:
                f.write("#!/bin/bash\n")
                f.write('echo "Running arXiv Query Script..."\n')
                f.write('cd "$(dirname "${BASH_SOURCE[0]:-$0}")"\n')
                f.write(f'"{sys.executable}" "{script_name}" --dir "{base_dir}"\n')
            
            # Make the .sh file executable
            st = os.stat(sh_path)
            os.chmod(sh_path, st.st_mode | stat.S_IEXEC)

BASE_DIR = os.path.abspath('./arXiv_data')
CACHE_FILE = os.path.join(BASE_DIR, 'arxiv_cache.json')
HTML_FILE = os.path.join(BASE_DIR, 'arxiv_homepage.html')
DEFAULT_CACHE_DAYS = 14
MAX_CACHE_DAYS = 28

def _clean_text(raw: str) -> str:
    """Strip leading/trailing whitespace and collapse all internal runs."""
    if not raw:
        return ""
    return re.sub(r'\s+', ' ', raw).strip()

DEFAULT_KEYWORDS = [
    "obscured",
    "active galactic nuclei", 
    "early universe", 
    "early times", 
    "kerr",
    "black hole"
]

def fetch_oai_pmh_papers(cutoff_date, with_status=False):
    """
    Harvests metadata from the arXiv OAI-PMH endpoint.
    Downloads the entire 'physics:astro-ph' set from the cutoff date, 
    then filters for GA (and excludes EP) client-side.
    """
    base_url = "https://export.arxiv.org/oai2"
    
    # Initial request parameters
    params = {
        'verb': 'ListRecords',
        'set': 'physics:astro-ph',
        'metadataPrefix': 'arXiv',
        'from': cutoff_date
    }
    
    ns = {
        'oai': 'http://www.openarchives.org/OAI/2.0/',
        'arxiv': 'http://arxiv.org/OAI/arXiv/'
    }
    
    new_papers = []
    complete = False
    
    while True:
        query_string = urllib.parse.urlencode(params)
        url = f"{base_url}?{query_string}"
        print(f"Harvesting OAI-PMH page: {url}")
        
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) arXiv-Dashboard-Bot/1.0'})
        
        try:
            response = urllib.request.urlopen(req, timeout=60)
            xml_data = response.read()
        except urllib.error.HTTPError as e:
            # OAI-PMH legally uses 503 with a Retry-After header for rate limiting
            if e.code == 503: 
                retry_after = int(e.headers.get('Retry-After', 10))
                print(f"HTTP 503: Server requested backoff. Waiting {retry_after} seconds...")
                time.sleep(retry_after)
                continue
            elif e.code == 429:
                print("HTTP 429: Too many requests. Waiting 15 seconds...")
                time.sleep(15)
                continue
            else:
                print(f"HTTP Error {e.code}: {e.reason}")
                break
        except Exception as e:
            print(f"Network error: {e}")
            break

        try:
            root = ET.fromstring(xml_data)
        except ET.ParseError as error:
            print(f"Invalid OAI-PMH response: {error}")
            break
        
        # Check for OAI-level errors (e.g., noRecordsMatch)
        error = root.find('oai:error', ns)
        if error is not None:
            if error.attrib.get('code') == 'noRecordsMatch':
                print("No new records found for this date range.")
                complete = True
            else:
                print(f"OAI Error: {error.attrib.get('code')} - {error.text}")
            break

        records = root.findall('.//oai:record', ns)
        for record in records:
            # Skip deleted records
            header = record.find('oai:header', ns)
            if header is not None and header.attrib.get('status') == 'deleted':
                continue
                
            metadata = record.find('oai:metadata/arxiv:arXiv', ns)
            if metadata is None:
                continue
                
            categories_text = metadata.find('arxiv:categories', ns).text or ""
            categories = categories_text.split()
            
            # --- CUSTOM LOGIC: Include GA, Exclude EP ---
            if 'astro-ph.GA' in categories and 'astro-ph.EP' not in categories:
                paper_id = metadata.find('arxiv:id', ns).text
                title    = _clean_text(metadata.find('arxiv:title',    ns).text)
                abstract = _clean_text(metadata.find('arxiv:abstract', ns).text)
                
                # Parse authors (OAI-PMH structures forenames and keynames separately)
                authors_list = []
                for author in metadata.findall('arxiv:authors/arxiv:author', ns):
                    keyname = author.find('arxiv:keyname', ns)
                    forenames = author.find('arxiv:forenames', ns)
                    name = ""
                    if forenames is not None and forenames.text: name += forenames.text + " "
                    if keyname is not None and keyname.text: name += keyname.text
                    if name: authors_list.append(name.strip())
                    
                # Use the OAI datestamp (announcement date), NOT the original submission date
                header = record.find('oai:header', ns)
                datestamp_elem = header.find('oai:datestamp', ns)
                published = datestamp_elem.text[:10] if datestamp_elem is not None else cutoff_date
                    
                new_papers.append({
                    'id': f"http://arxiv.org/abs/{paper_id}",
                    'title': title,
                    'abstract': abstract,
                    'authors': ', '.join(authors_list),
                    'published': published,
                    'doi': _clean_text(metadata.findtext('arxiv:doi', '', ns)),
                    'author_orcids': sorted({
                        normalize_orcid(element.text)
                        for author in metadata.findall('arxiv:authors/arxiv:author', ns)
                        for element in author.iter()
                        if element.tag.rsplit('}', 1)[-1].lower() == 'orcid'
                        and normalize_orcid(element.text)
                    }),
                })

        # Check for pagination (resumptionToken)
        token_element = root.find('.//oai:resumptionToken', ns)
        if token_element is not None and token_element.text:
            token = token_element.text
            # When using a resumption token, all other params MUST be omitted
            params = {'verb': 'ListRecords', 'resumptionToken': token} 
            time.sleep(5)  # Mandatory courtesy delay between pages
        else:
            complete = True
            break  # No more pages

    return (new_papers, complete) if with_status else new_papers

def fetch_and_cache_papers():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            cache = json.load(f)
    else:
        cache = []

    # Retain the maximum selectable window so HTML changes need no re-harvest.
    purge_cutoff = (datetime.now(timezone.utc) - timedelta(days=MAX_CACHE_DAYS - 1)).strftime('%Y-%m-%d')
    original_count = len(cache)
    cache = [paper for paper in cache if paper['published'] >= purge_cutoff]
    if len(cache) < original_count:
        print(f"Purged {original_count - len(cache)} old papers from the local cache.")

    known_ids = {paper['id'] for paper in cache}
    coverage_file = os.path.join(BASE_DIR, 'cache_metadata.json')
    coverage = {}
    if os.path.exists(coverage_file):
        try:
            with open(coverage_file, encoding='utf-8') as source:
                coverage = json.load(source)
        except (OSError, ValueError):
            pass

    # Revisit the last inclusive date to catch updates. Backfill the full window
    # once when migrating caches that predate DOI/ORCID metadata support.
    if (cache and all('doi' in p and 'author_orcids' in p for p in cache)
            and coverage.get('coverage_from', '9999-12-31') <= purge_cutoff):
        last_date = max(paper['published'] for paper in cache)
        query_cutoff = (datetime.strptime(last_date, '%Y-%m-%d')).strftime('%Y-%m-%d')
        print(f"Checking arXiv OAI-PMH for new/updated astro-ph papers since {query_cutoff}...")
    else:
        query_cutoff = purge_cutoff
        print(f"New cache or metadata upgrade — harvesting since {query_cutoff}...")

    if coverage.get('retry_from'):
        query_cutoff = max(purge_cutoff, min(query_cutoff, coverage['retry_from']))
    harvested_papers, complete = fetch_oai_pmh_papers(query_cutoff, with_status=True)
    
    # Refresh existing records too, so older caches gain DOI/ORCID fields.
    updated_by_id = {p['id']: p for p in cache}
    updated_by_id.update({p['id']: p for p in harvested_papers})
    new_papers = [p for p in harvested_papers if p['id'] not in known_ids]
    
    if new_papers:
        print(f"Found {len(new_papers)} new GA papers! Updating cache...")
    cache = list(updated_by_id.values())
        
    # Sort and save
    cache.sort(key=lambda x: x['published'], reverse=True)
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=4)
    if complete:
        coverage = {'coverage_from': min(coverage.get('coverage_from', query_cutoff), query_cutoff)}
    else:
        coverage['retry_from'] = query_cutoff
    with open(coverage_file, 'w', encoding='utf-8') as output:
        json.dump(coverage, output, indent=2)
            
    return cache

def _script_json(value):
    return json.dumps(value).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')


def generate_single_html(cache, tracked_pis=None, orcid_index=None, orcid_status=None, cache_days=DEFAULT_CACHE_DAYS):
    print("Generating the single-page application...")
    tracked_pis = tracked_pis or []
    orcid_index = orcid_index or {}
    orcid_status = orcid_status or {}
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>My arXiv Dashboard</title>
        
        <script>
            MathJax = {{
                tex: {{
                    inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
                    displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']]
                }}
            }};
        </script>
        <script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script>
        
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #f4f4f9; color: #333; max-width: 900px; margin: 0 auto; padding: 20px; line-height: 1.6; }}
            h1 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }}
            .header-info {{ display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 20px; }}
            .last-updated {{ font-size: 0.9em; color: #7f8c8d; text-align: right; }}
            
            /* UI Panels */
            .control-panel {{ display: flex; gap: 20px; margin-bottom: 20px; flex-wrap: wrap; }}
            .panel-box {{ background: #fff; padding: 15px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); flex: 1; min-width: 300px; }}
            .panel-box h3 {{ margin-top: 0; font-size: 1.1em; margin-bottom: 10px; }}
            
            /* Keyword Manager */
            .keyword-box {{ border-left: 4px solid #e67e22; }}
            .keyword-box h3 {{ color: #d35400; }}
            .keyword-input-group {{ display: flex; gap: 10px; margin-bottom: 10px; }}
            #newKeywordInput {{ flex-grow: 1; padding: 8px; border: 1px solid #bdc3c7; border-radius: 4px; }}
            .btn-orange {{ background-color: #e67e22; color: white; border: none; padding: 8px 15px; border-radius: 4px; cursor: pointer; font-weight: bold; }}
            .keyword-list {{ display: flex; flex-wrap: wrap; gap: 8px; }}
            .keyword-badge {{ background-color: #ffeaa7; color: #d35400; padding: 5px 10px; border-radius: 15px; font-size: 0.9em; font-weight: bold; display: flex; align-items: center; gap: 6px; }}
            .remove-kw {{ cursor: pointer; color: #c0392b; font-weight: bold; border-radius: 50%; width: 16px; height: 16px; display: inline-flex; align-items: center; justify-content: center; }}
            
            /* Live Query Builder */
            .query-box {{ border-left: 4px solid #8e44ad; }}
            .query-box h3 {{ color: #8e44ad; }}
            .cat-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 15px; font-size: 0.9em; }}
            .cat-item {{ display: flex; justify-content: space-between; align-items: center; }}
            .cat-item select {{ padding: 4px; border-radius: 4px; border: 1px solid #bdc3c7; }}
            .action-buttons {{ display: flex; gap: 10px; }}
            .btn-purple {{ background-color: #8e44ad; color: white; border: none; padding: 8px 15px; border-radius: 4px; cursor: pointer; flex: 1; font-weight: bold; }}
            .btn-green {{ background-color: #27ae60; color: white; border: none; padding: 8px 15px; border-radius: 4px; cursor: pointer; flex: 1; font-weight: bold; }}
            .btn-green:hover {{ background-color: #2ecc71; }}
            #loadingIndicator {{ text-align: center; color: #8e44ad; font-weight: bold; margin-top: 10px; display: none; }}

            /* Search & Papers */
            .search-container {{ margin-bottom: 30px; }}
            #searchInput {{ width: 100%; padding: 12px 15px; font-size: 16px; border: 2px solid #bdc3c7; border-radius: 6px; outline: none; }}
            
            .paper {{ background: #fff; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); display: none; position: relative; }}
            .paper h2 {{ margin-top: 0; color: #2980b9; font-size: 1.3em; padding-right: 80px; }}
            .paper a {{ color: inherit; text-decoration: none; }}
            .match-badge {{ position: absolute; top: 20px; right: 20px; background: #e67e22; color: white; padding: 4px 8px; border-radius: 4px; font-size: 0.8em; font-weight: bold; display: none; }}
            .authors {{ font-style: italic; color: #555; margin-bottom: 10px; font-size: 0.95em; }}
            .date {{ display: inline-block; background: #ecf0f1; padding: 3px 8px; border-radius: 4px; font-size: 0.8em; color: #7f8c8d; margin-bottom: 10px; }}
            .abstract {{ color: #444; text-align: justify; }}
            .highlight {{ background-color: #ffeaa7; font-weight: bold; color: #d35400; padding: 0 3px; border-radius: 3px; }}
            .orcid-box {{ border-left: 4px solid #638b21; margin-bottom: 20px; }}
            .orcid-matches {{ color: #45651b; font-size: 0.9em; margin-bottom: 10px; }}
            .orcid-matches a {{ text-decoration: underline; }}
            .pi-table-wrap {{ max-height: 300px; overflow: auto; margin-top: 10px; }}
            .pi-table {{ width: 100%; border-collapse: collapse; font-size: 0.85em; }}
            .pi-table th, .pi-table td {{ text-align: left; padding: 6px; border-bottom: 1px solid #ddd; }}
            .pi-table input, .pi-table select {{ box-sizing: border-box; width: 100%; min-width: 120px; padding: 7px; border: 1px solid #bdc3c7; border-radius: 4px; }}
            .pi-table .pi-orcid {{ min-width: 190px; }}
            .pi-tools {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }}
            .pi-tools button, .pi-table button {{ padding: 7px 10px; border: 1px solid #bdc3c7; border-radius: 4px; cursor: pointer; }}
            .pi-tools .pi-save {{ background: #638b21; color: white; border-color: #638b21; }}
            .orcid-box summary {{ cursor: pointer; font-weight: 600; color: #45651b; }}
            #piMessage {{ font-size: 0.9em; white-space: pre-line; }}
            .ranking-note, #orcidStatus {{ font-size: 0.9em; color: #555; }}
            .cache-controls {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin: 20px 0; font-size: 0.9em; }}
            #cacheDays {{ width: 65px; padding: 7px; border: 1px solid #bdc3c7; border-radius: 4px; }}
            .cache-controls button {{ padding: 7px 10px; border: 1px solid #bdc3c7; border-radius: 4px; cursor: pointer; }}
            
            /* Pagination */
            .pagination {{ display: flex; justify-content: space-between; align-items: center; padding: 20px; background: #fff; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
            .pagination button {{ padding: 10px 20px; background-color: #3498db; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; }}
            .pagination button:disabled {{ background-color: #ecf0f1; color: #bdc3c7; cursor: not-allowed; }}
            .page-info {{ font-weight: bold; color: #2c3e50; }}
        </style>
    </head>
    <body>
        <div class="header-info">
            <h1>Astrophysics Daily</h1>
            <div class="last-updated">Local cache: {len(cache)} papers (up to 28 days)<br>Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
        </div>
        
        <div class="control-panel">
            <div class="panel-box keyword-box">
                <h3>Keywords (primary ranking)</h3>
                <div class="keyword-input-group">
                    <input type="text" id="newKeywordInput" placeholder="Add keyword to prioritize...">
                    <button class="btn-orange" onclick="addKeyword()">Add</button>
                </div>
                <div class="keyword-list" id="keywordList"></div>
            </div>

            <div class="panel-box query-box">
                <h3>Live arXiv Query</h3>
                <div class="cat-grid">
                    <div class="cat-item">
                        <span><strong>GA</strong> (Galaxies)</span>
                        <select id="cat-ga">
                            <option value="AND" selected>Include</option>
                            <option value="ANDNOT">Exclude</option>
                            <option value="IGNORE">Ignore</option>
                        </select>
                    </div>
                    <div class="cat-item">
                        <span><strong>CO</strong> (Cosmology)</span>
                        <select id="cat-co">
                            <option value="AND">Include</option>
                            <option value="ANDNOT">Exclude</option>
                            <option value="IGNORE" selected>Ignore</option>
                        </select>
                    </div>
                    <div class="cat-item">
                        <span><strong>SR</strong> (Solar/Stellar)</span>
                        <select id="cat-sr">
                            <option value="AND">Include</option>
                            <option value="ANDNOT">Exclude</option>
                            <option value="IGNORE" selected>Ignore</option>
                        </select>
                    </div>
                    <div class="cat-item">
                        <span><strong>EP</strong> (Earth/Planets)</span>
                        <select id="cat-ep">
                            <option value="AND">Include</option>
                            <option value="ANDNOT" selected>Exclude</option>
                            <option value="IGNORE">Ignore</option>
                        </select>
                    </div>
                    <div class="cat-item">
                        <span><strong>HE</strong> (High Energy)</span>
                        <select id="cat-he">
                            <option value="AND">Include</option>
                            <option value="ANDNOT">Exclude</option>
                            <option value="IGNORE" selected>Ignore</option>
                        </select>
                    </div>
                    <div class="cat-item">
                        <span><strong>IM</strong> (Instrumentation)</span>
                        <select id="cat-im">
                            <option value="AND">Include</option>
                            <option value="ANDNOT">Exclude</option>
                            <option value="IGNORE" selected>Ignore</option>
                        </select>
                    </div>
                </div>
                <div class="action-buttons">
                    <button class="btn-purple" onclick="fetchLiveArxiv()">Go (Live Fetch)</button>
                    <button class="btn-green" onclick="downloadResults()">Download Results</button>
                </div>
                <div id="loadingIndicator">Fetching & Parsing from arXiv...</div>
            </div>
        </div>

        <div class="panel-box orcid-box">
            <details id="piEditor"><summary>PI ORCID tracking (secondary) — edit watchlist and priorities</summary>
                <p class="ranking-note">Keywords rank first. PI score breaks ties: Highest = 3, High = 2, Medium = 1, Skip = 0.
                    Each matched PI counts once. Edit the list and save to apply changes in this browser.</p>
                <label><input type="checkbox" id="orcidEnabled" checked onchange="togglePIRanking()"> Use PI priority to break keyword-score ties</label>
                <p id="orcidStatus"></p>
                <div class="pi-tools">
                    <button class="pi-save" onclick="savePIChanges()">Save changes</button>
                    <button onclick="addPIRow()">Add PI</button>
                    <button id="refreshPIBtn" onclick="refreshPIWorks(true)">Refresh ORCID works</button>
                    <button onclick="downloadPIConfig()">Export watchlist</button>
                    <button onclick="resetPIWatchlist()">Reset to tracker defaults</button>
                </div>
                <p id="piMessage" role="status" aria-live="polite"></p>
                <div class="pi-table-wrap"><table class="pi-table">
                    <thead><tr><th>PI</th><th>Priority</th><th>ORCID iD or URL</th><th>Action</th></tr></thead>
                    <tbody id="piWatchlist"></tbody>
                </table></div>
            </details>
        </div>

        <div class="cache-controls">
            <label for="cacheDays">Cache window: <input type="number" id="cacheDays" min="7" max="28" step="1" value="{cache_days}" onchange="updateCacheWindow()"> days (7–28)</label>
            <button onclick="showCachedPapers()">Show local cache</button>
            <span id="cacheWindowSummary" role="status"></span>
        </div>

        <div class="search-container">
            <input type="text" id="searchInput" placeholder="Search authors, abstracts, or titles..." onkeyup="filterPapers()">
        </div>

        <div id="paperList">
    """
    
    for index, paper in enumerate(cache):
        html_content += f"""
            <div class="paper" data-original-index="{index}">
                <div class="match-badge">0 Matches</div>
                <h2><a href="{escape(paper['id'], quote=True)}" target="_blank">{escape(paper['title'])}</a></h2>
                <div class="date">Announced: {escape(paper['published'])}</div>
                <div class="authors">{escape(paper['authors'])}</div>
                <div class="orcid-matches"></div>
                <div class="abstract">
                    <strong>Abstract:</strong> <span class="abstract-text"></span>
                </div>
            </div>
        """
        
    html_content += f"""
        </div>
        
        <div class="pagination">
            <button id="prevBtn" onclick="prevPage()">&laquo; Previous</button>
            <span class="page-info" id="pageInfo">Page 1</span>
            <button id="nextBtn" onclick="nextPage()">Next &raquo;</button>
        </div>

        <script>
            // --- GLOBAL VARIABLES & INIT ---
            const papersPerPage = 50;
            let currentPage = 1;
            let allPapers = [];
            let filteredPapers = [];
            
            // This holds the clean JSON data of whatever is currently displayed (cache or live query)
            let currentDataset = {_script_json(cache)};
            const cachedDataset = currentDataset;
            const defaultCacheDays = {int(cache_days)};
            let activeCacheDays = defaultCacheDays;
            let datasetMode = 'cache';
            const defaultPIs = {_script_json(tracked_pis)};
            const priorityWeights = {_script_json(PRIORITY_WEIGHTS)};
            let trackedPIs = defaultPIs.map(pi => ({{...pi}}));
            const orcidIndex = {_script_json(orcid_index)};
            const orcidStatus = {_script_json(orcid_status)};
            let browserWorks = {{}};
            let refreshingPIs = false;
            let piEditorDirty = false;
            
            const defaultKeywords = {json.dumps(DEFAULT_KEYWORDS)};
            let activeKeywords = [];
            
            window.onload = function() {{
                allPapers = Array.from(document.getElementsByClassName('paper'));
                
                // Assign abstracts safely from currentDataset to avoid quote conflicts
                const abstractSpans = document.querySelectorAll('.abstract-text');
                abstractSpans.forEach((span, i) => {{
                    span.textContent = currentDataset[i].abstract;
                    span.dataset.originalText = currentDataset[i].abstract;
                }});

                const storedKw = readStored('arxiv_keywords', defaultKeywords);
                activeKeywords = Array.isArray(storedKw) ? storedKw.filter(kw => typeof kw === 'string' && kw.trim()) : defaultKeywords;
                try {{ trackedPIs = validatePIs(readStored('arxiv_pi_watchlist_v1', defaultPIs)); }}
                catch (_) {{ trackedPIs = defaultPIs.map(pi => ({{...pi}})); }}
                document.getElementById('orcidEnabled').checked = readStored('arxiv_pi_enabled_v1', true) !== false;
                const storedDays = Number(readStored('arxiv_cache_days_v1', defaultCacheDays));
                activeCacheDays = Number.isInteger(storedDays) && storedDays >= 7 && storedDays <= 28 ? storedDays : defaultCacheDays;
                document.getElementById('cacheDays').value = activeCacheDays;
                browserWorks = readStored('arxiv_orcid_works_v1', {{}});
                if (!browserWorks || typeof browserWorks !== 'object' || Array.isArray(browserWorks)) browserWorks = {{}};
                Object.entries(browserWorks).forEach(([id, record]) => {{
                    if (normalizeOrcid(id) && record && Array.isArray(record.keys) && record.fetched_at > (orcidStatus[id]?.fetched_at || 0)) {{
                        replaceWorkIndex(id, record);
                    }}
                }});

                renderKeywordUI();
                renderPIWatchlist();
                applyHighlightsAndRender();
                
                document.getElementById("newKeywordInput").addEventListener("keyup", function(event) {{
                    if (event.key === "Enter") addKeyword();
                }});
            }};

            // --- DOWNLOADING DATA ---
            function downloadResults() {{
                // Export the selected date/search window in its displayed ranking.
                const results = filteredPapers.map(paper => currentDataset[Number(paper.dataset.originalIndex)]);
                const dataStr = JSON.stringify(results, null, 4);
                const blob = new Blob([dataStr], {{ type: "application/json" }});
                const url = URL.createObjectURL(blob);
                
                // Create a temporary link and trigger the download
                const a = document.createElement('a');
                a.href = url;
                a.download = "arxiv_custom_results_" + new Date().toISOString().slice(0,10) + ".json";
                document.body.appendChild(a);
                a.click();
                
                // Clean up
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
            }}

            // --- LIVE ARXIV QUERY LOGIC ---
            async function fetchLiveArxiv() {{
                const loadingInd = document.getElementById('loadingIndicator');
                loadingInd.style.display = 'block';
                loadingInd.innerText = 'Fetching & Parsing from arXiv...';
                
                let included = [];
                let excluded = [];
                
                const categories = [
                    {{ id: 'cat-co', val: 'cat:astro-ph.CO' }},
                    {{ id: 'cat-ga', val: 'cat:astro-ph.GA' }},
                    {{ id: 'cat-sr', val: 'cat:astro-ph.SR' }},
                    {{ id: 'cat-ep', val: 'cat:astro-ph.EP' }},
                    {{ id: 'cat-he', val: 'cat:astro-ph.HE' }},
                    {{ id: 'cat-im', val: 'cat:astro-ph.IM' }}
                ];
                
                categories.forEach(cat => {{
                    const status = document.getElementById(cat.id).value;
                    if (status === 'AND') included.push(cat.val);
                    if (status === 'ANDNOT') excluded.push(cat.val);
                }});

                let queryStr = included.join(' AND ');
                if (excluded.length > 0) {{
                    if (queryStr.length > 0) queryStr += ' ANDNOT ';
                    queryStr += excluded.join(' ANDNOT ');
                }}
                if (queryStr.startsWith('ANDNOT')) {{ queryStr = 'all:astro-ph ' + queryStr; }}

                // Direct fetch to the arXiv Atom API over HTTPS — no third-party proxy needed.
                // The arXiv API sets permissive CORS headers, so this works from any browser.
                const arxivUrl = `https://export.arxiv.org/api/query?search_query=${{encodeURIComponent(queryStr)}}&start=0&max_results=500&sortBy=submittedDate&sortOrder=descending`;

                let maxRetries = 3;
                let attempt = 0;
                let xmlStr = null;
                
                while (attempt < maxRetries) {{
                    try {{
                        const response = await fetch(arxivUrl);
                        
                        // Handle HTTP-level rate limiting
                        if (response.status === 429 || response.status === 503) {{
                            attempt++;
                            let waitTime = attempt * 10;
                            loadingInd.innerText = `Rate limited by arXiv (HTTP ${{response.status}}). Retrying in ${{waitTime}}s...`;
                            await new Promise(r => setTimeout(r, waitTime * 1000));
                            continue;
                        }}
                        if (!response.ok) throw new Error(`HTTP error! status: ${{response.status}}`);
                        
                        xmlStr = await response.text();
                        
                        // Guard against an unexpected non-XML payload
                        if (!xmlStr.trim().startsWith('<')) {{
                            attempt++;
                            let waitTime = attempt * 10;
                            loadingInd.innerText = `Unexpected response from arXiv. Retrying in ${{waitTime}}s...`;
                            await new Promise(r => setTimeout(r, waitTime * 1000));
                            continue;
                        }}
                        
                        break; // Success!
                        
                    }} catch(e) {{
                        attempt++;
                        if (attempt >= maxRetries) {{
                            alert("Network error. Try again later. " + e);
                            loadingInd.style.display = 'none';
                            return;
                        }}
                        loadingInd.innerText = `Network error. Retrying... (${{attempt}}/${{maxRetries}})`;
                        await new Promise(r => setTimeout(r, 5000));
                    }}
                }}
                
                if (!xmlStr) {{
                    loadingInd.style.display = 'none';
                    return;
                }}
                
                try {{
                    const data = new window.DOMParser().parseFromString(xmlStr, "text/xml");
                    const entries = data.querySelectorAll("entry");
                    
                    const newPapers = [];
                    entries.forEach(entry => {{
                        const id = entry.querySelector("id").textContent;
                        const title = entry.querySelector("title").textContent.replace(/\\n/g, ' ').trim();
                        const abstract = entry.querySelector("summary").textContent.replace(/\\n/g, ' ').trim();
                        const published = entry.querySelector("published").textContent.substring(0, 10);
                        const authors = Array.from(entry.querySelectorAll("author name")).map(n => n.textContent).join(', ');
                        
                        const doi = entry.getElementsByTagNameNS('http://arxiv.org/schemas/atom', 'doi')[0]?.textContent || '';
                        const author_orcids = Array.from(entry.querySelectorAll('author')).flatMap(author =>
                            Array.from(author.getElementsByTagName('*')).filter(el => el.localName.toLowerCase() === 'orcid')
                                .map(el => normalizeOrcid(el.textContent)).filter(Boolean));
                        newPapers.push({{id, title, abstract, published, authors, doi, author_orcids}});
                    }});
                    
                    if(newPapers.length === 0) {{ alert("Query successful, but no papers matched those exact rules."); }}
                    else {{ 
                        currentDataset = newPapers; // Update the global dataset for the download button
                        datasetMode = 'live';
                        document.getElementById('cacheDays').disabled = true;
                        rebuildDOM(newPapers); 
                    }}
                    
                }} catch(e) {{
                    alert("Error parsing the XML response. " + e);
                }}
                
                loadingInd.style.display = 'none';
            }}

            function rebuildDOM(papers) {{
                const list = document.getElementById('paperList');
                list.innerHTML = ''; 
                
                papers.forEach((paper, index) => {{
                    const div = document.createElement('div');
                    div.className = 'paper';
                    div.dataset.originalIndex = index;
                    
                    div.innerHTML = `
                        <div class="match-badge">0 Matches</div>
                        <h2><a href="${{escapeHTML(paper.id)}}" target="_blank">${{escapeHTML(paper.title)}}</a></h2>
                        <div class="date">Announced: ${{escapeHTML(paper.published)}}</div>
                        <div class="authors">${{escapeHTML(paper.authors)}}</div>
                        <div class="orcid-matches"></div>
                        <div class="abstract">
                            <strong>Abstract:</strong> <span class="abstract-text"></span>
                        </div>
                    `;
                    
                    const span = div.querySelector('.abstract-text');
                    span.textContent = paper.abstract;
                    span.dataset.originalText = paper.abstract;
                    list.appendChild(div);
                }});
                
                allPapers = Array.from(document.getElementsByClassName('paper'));
                document.getElementById('searchInput').value = ''; 
                applyHighlightsAndRender();
            }}

            // --- KEYWORD MANAGEMENT ---
            function renderKeywordUI() {{
                const list = document.getElementById('keywordList');
                list.innerHTML = '';
                activeKeywords.forEach(kw => {{
                    const badge = document.createElement('div');
                    badge.className = 'keyword-badge';
                    badge.textContent = kw + ' ';
                    const remove = document.createElement('span');
                    remove.className = 'remove-kw';
                    remove.textContent = '×';
                    remove.title = 'Remove';
                    remove.onclick = () => removeKeyword(kw);
                    badge.appendChild(remove);
                    list.appendChild(badge);
                }});
            }}

            function addKeyword() {{
                const input = document.getElementById('newKeywordInput');
                const newKw = input.value.trim().toLowerCase();
                if (newKw && !activeKeywords.includes(newKw)) {{
                    activeKeywords.push(newKw);
                    localStorage.setItem('arxiv_keywords', JSON.stringify(activeKeywords));
                    renderKeywordUI();
                    applyHighlightsAndRender();
                }}
                input.value = '';
            }}

            function removeKeyword(kwToRemove) {{
                activeKeywords = activeKeywords.filter(kw => kw !== kwToRemove);
                localStorage.setItem('arxiv_keywords', JSON.stringify(activeKeywords));
                renderKeywordUI();
                applyHighlightsAndRender();
            }}
            
            function escapeRegExp(string) {{ return string.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&'); }}

            function escapeHTML(value) {{
                return String(value ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}}[c]));
            }}

            function normalizeOrcid(value) {{
                const id = String(value || '').trim().replace(/^https?:\\/\\/(?:www\\.)?orcid\\.org\\//i, '').replace(/\\/$/, '').toUpperCase();
                if (!/^\\d{{4}}-\\d{{4}}-\\d{{4}}-\\d{{3}}[\\dX]$/.test(id)) return '';
                const digits = id.replace(/-/g, '');
                let total = 0;
                for (const digit of digits.slice(0, -1)) total = (total + Number(digit)) * 2;
                const check = (12 - total % 11) % 11;
                return digits[15] === (check === 10 ? 'X' : String(check)) ? id : '';
            }}

            function normalizeArxiv(value) {{
                let id = String(value || '').trim();
                try {{ id = decodeURIComponent(id); }} catch (_) {{ return ''; }}
                id = id.replace(/^https?:\\/\\/(?:export\\.)?arxiv\\.org\\/(?:abs|pdf)\\//i, '')
                    .replace(/^arxiv:\\s*/i, '').replace(/\\.pdf$/i, '').replace(/v\\d+$/, '');
                return /^(?:\\d{{4}}\\.\\d{{4,5}}|[a-zA-Z.-]+\\/\\d{{7}})$/.test(id) ? id.toLowerCase() : '';
            }}

            function normalizeDoi(value) {{
                let doi = String(value || '').trim();
                try {{ doi = decodeURIComponent(doi); }} catch (_) {{ return ''; }}
                doi = doi.toLowerCase().replace(/^(?:https?:\\/\\/(?:dx\\.)?doi\\.org\\/|doi:\\s*)/, '');
                return /^10\\.\\d{{4,9}}\\/\\S+$/.test(doi) ? doi : '';
            }}

            function getPIMatches(paper) {{
                const ids = new Set((paper.author_orcids || []).map(normalizeOrcid).filter(Boolean));
                const keys = ['arxiv:' + normalizeArxiv(paper.id), 'doi:' + normalizeDoi(paper.doi)];
                keys.forEach(key => (orcidIndex[key] || []).forEach(id => ids.add(id)));
                return trackedPIs.filter(pi => pi.weight > 0 && pi.orcid && ids.has(pi.orcid))
                    .sort((a, b) => b.weight - a.weight || a.name.localeCompare(b.name));
            }}

            function readStored(key, fallback) {{
                try {{ const value = localStorage.getItem(key); return value === null ? fallback : JSON.parse(value); }}
                catch (_) {{ return fallback; }}
            }}

            function saveStored(key, value) {{
                try {{ localStorage.setItem(key, JSON.stringify(value)); return true; }}
                catch (_) {{ return false; }}
            }}

            function validatePIs(people) {{
                if (!Array.isArray(people)) throw new Error('The watchlist must be a list.');
                const seen = new Set();
                return people.map((pi, i) => {{
                    const name = String(pi.name || '').trim();
                    if (!name) throw new Error(`Row ${{i + 1}} needs a PI name.`);
                    if (!Object.hasOwn(priorityWeights, pi.priority)) throw new Error(`Choose a priority for ${{name}}.`);
                    const id = normalizeOrcid(pi.orcid);
                    if (pi.orcid && !id) throw new Error(`Invalid ORCID for ${{name}}. Check the 16-digit iD (including its check digit).`);
                    if (id && seen.has(id)) throw new Error(`ORCID ${{id}} appears more than once. Keep one row per PI.`);
                    if (id) seen.add(id);
                    return {{...pi, name, orcid: id || null, weight: priorityWeights[pi.priority]}};
                }});
            }}

            function updatePIStatus() {{
                const active = trackedPIs.filter(pi => pi.weight > 0);
                const resolved = active.filter(pi => pi.orcid);
                const unavailable = resolved.filter(pi => !orcidStatus[pi.orcid] || orcidStatus[pi.orcid].status === 'unavailable').length;
                const stale = resolved.filter(pi => orcidStatus[pi.orcid]?.fetched_at && Date.now() / 1000 - orcidStatus[pi.orcid].fetched_at >= 86400).length;
                const empty = resolved.filter(pi => orcidStatus[pi.orcid]?.identifier_count === 0).length;
                document.getElementById('orcidStatus').textContent =
                    `${{resolved.length}} of ${{active.length}} active PIs have an ORCID; ${{active.length - resolved.length}} need an ID. ` +
                    `${{unavailable}} works records unavailable; ${{stale}} cached over 24 hours ago; ${{empty}} have no public work identifiers. ` +
                    'Matches use exact arXiv/DOI links in public ORCID works or supplied author ORCIDs. Public records can be incomplete. Use Refresh ORCID works to update them.';
            }}

            function appendPIRow(pi = {{name: '', orcid: '', priority: 'Medium'}}) {{
                const row = document.createElement('tr');
                row._person = pi;
                row.innerHTML = `<td><input class="pi-name" aria-label="PI name" value="${{escapeHTML(pi.name)}}" title="${{escapeHTML(pi.institution || '')}}"></td>` +
                    `<td><select class="pi-priority" aria-label="PI priority">${{Object.keys(priorityWeights).map(priority =>
                        `<option value="${{escapeHTML(priority)}}" ${{priority === pi.priority ? 'selected' : ''}}>${{escapeHTML(priority)}} (${{priorityWeights[priority]}})</option>`).join('')}}</select></td>` +
                    `<td><input class="pi-orcid" aria-label="ORCID iD" value="${{escapeHTML(pi.orcid || '')}}" placeholder="0000-0000-0000-0000"></td><td><button type="button">Remove</button></td>`;
                row.addEventListener('input', markPIDirty);
                row.addEventListener('change', markPIDirty);
                row.querySelector('button').onclick = () => {{ row.remove(); markPIDirty(); }};
                document.getElementById('piWatchlist').appendChild(row);
                return row;
            }}

            function markPIDirty() {{
                piEditorDirty = true;
                document.getElementById('piMessage').textContent = 'Unsaved changes. Save to apply the watchlist and priorities.';
            }}

            function addPIRow() {{
                const row = appendPIRow();
                markPIDirty();
                row.querySelector('.pi-name').focus();
                row.scrollIntoView({{block: 'nearest'}});
            }}

            function renderPIWatchlist() {{
                const body = document.getElementById('piWatchlist');
                body.innerHTML = '';
                trackedPIs.forEach(appendPIRow);
                piEditorDirty = false;
                updatePIStatus();
            }}

            async function savePIChanges() {{
                if (refreshingPIs) {{ document.getElementById('piMessage').textContent = 'Wait for the ORCID refresh to finish, then save your changes.'; return; }}
                try {{
                    const draft = Array.from(document.querySelectorAll('#piWatchlist tr')).map(row => {{
                        const pi = {{...row._person, name: row.querySelector('.pi-name').value.trim(),
                            orcid: row.querySelector('.pi-orcid').value.trim(), priority: row.querySelector('.pi-priority').value}};
                        if (normalizeOrcid(pi.orcid) !== normalizeOrcid(row._person.orcid) || pi.name !== row._person.name) {{
                            delete pi.orcid_source;
                            delete pi.source_row;
                            delete pi.source_sheet;
                        }}
                        return pi;
                    }});
                    trackedPIs = validatePIs(draft);
                    const saved = saveStored('arxiv_pi_watchlist_v1', trackedPIs);
                    renderPIWatchlist();
                    applyHighlightsAndRender();
                    document.getElementById('piMessage').textContent = saved ? 'Saved in this browser. Ranking updated.' :
                        'Ranking updated for this session. Browser storage is unavailable; export the watchlist to keep your changes.';
                    await refreshPIWorks(false);
                }} catch (error) {{ document.getElementById('piMessage').textContent = error.message; }}
            }}

            function togglePIRanking() {{
                saveStored('arxiv_pi_enabled_v1', document.getElementById('orcidEnabled').checked);
                applyHighlightsAndRender();
            }}

            function resetPIWatchlist() {{
                trackedPIs = defaultPIs.map(pi => ({{...pi}}));
                const saved = saveStored('arxiv_pi_watchlist_v1', trackedPIs);
                renderPIWatchlist();
                applyHighlightsAndRender();
                document.getElementById('piMessage').textContent = saved ? 'Tracker defaults restored and saved.' : 'Tracker defaults restored for this session; browser storage is unavailable.';
            }}

            function downloadPIConfig() {{
                if (piEditorDirty) {{ document.getElementById('piMessage').textContent = 'Save your changes before exporting.'; return; }}
                const blob = new Blob([JSON.stringify({{people: trackedPIs}}, null, 2)], {{type: 'application/json'}});
                const url = URL.createObjectURL(blob);
                const link = document.createElement('a');
                link.href = url;
                link.download = 'tracked_pis.json';
                document.body.appendChild(link);
                link.click();
                link.remove();
                URL.revokeObjectURL(url);
            }}

            function publicWorkKeys(payload) {{
                const keys = new Set();
                for (const group of payload.group || []) {{
                    for (const entry of [group, ...(group['work-summary'] || [])]) {{
                        for (const external of entry['external-ids']?.['external-id'] || []) {{
                            if (!['self', 'version-of'].includes(external['external-id-relationship'])) continue;
                            const kind = (external['external-id-type'] || '').toLowerCase();
                            const value = external['external-id-value'];
                            if (kind === 'arxiv') {{
                                const id = normalizeArxiv(value);
                                if (id) keys.add('arxiv:' + id);
                            }} else if (kind === 'doi') {{
                                const doi = normalizeDoi(value);
                                if (doi) keys.add('doi:' + doi);
                                if (doi.startsWith('10.48550/arxiv.')) {{
                                    const id = normalizeArxiv(doi.slice('10.48550/arxiv.'.length));
                                    if (id) keys.add('arxiv:' + id);
                                }}
                            }}
                        }}
                        const id = normalizeArxiv(entry.url?.value);
                        if (id) keys.add('arxiv:' + id);
                    }}
                }}
                return [...keys].sort();
            }}

            function replaceWorkIndex(id, record) {{
                Object.keys(orcidIndex).forEach(key => {{
                    orcidIndex[key] = orcidIndex[key].filter(value => value !== id);
                    if (!orcidIndex[key].length) delete orcidIndex[key];
                }});
                record.keys.filter(key => typeof key === 'string' && /^(arxiv|doi):/.test(key)).forEach(key => {{
                    if (!Object.hasOwn(orcidIndex, key)) orcidIndex[key] = [];
                    orcidIndex[key].push(id);
                }});
                orcidStatus[id] = {{status: 'current', fetched_at: record.fetched_at, identifier_count: record.keys.length}};
            }}

            async function refreshPIWorks(force = true) {{
                if (refreshingPIs) return;
                if (force && piEditorDirty) {{ document.getElementById('piMessage').textContent = 'Save your changes before refreshing.'; return; }}
                const pending = trackedPIs.filter(pi => pi.weight > 0 && pi.orcid &&
                    (force || !orcidStatus[pi.orcid]?.fetched_at || Date.now() / 1000 - orcidStatus[pi.orcid].fetched_at >= 86400));
                if (!pending.length) return;
                refreshingPIs = true;
                const button = document.getElementById('refreshPIBtn');
                const message = document.getElementById('piMessage');
                button.disabled = true;
                let completed = 0;
                let failure = '';
                try {{
                    for (const pi of pending) {{
                        message.textContent = `Refreshing ORCID works: ${{pi.name}} (${{completed + 1}}/${{pending.length}})...`;
                        const controller = new AbortController();
                        const timeout = setTimeout(() => controller.abort(), 20000);
                        try {{
                            const response = await fetch(`https://pub.orcid.org/v3.0/${{pi.orcid}}/works`, {{
                                headers: {{Accept: 'application/json'}}, signal: controller.signal
                            }});
                            if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
                            const payload = await response.json();
                            if (!payload || !Array.isArray(payload.group)) throw new Error('Unexpected ORCID response');
                            const record = {{keys: publicWorkKeys(payload), fetched_at: Date.now() / 1000}};
                            browserWorks[pi.orcid] = record;
                            replaceWorkIndex(pi.orcid, record);
                            completed++;
                        }} catch (error) {{
                            failure = `Could not refresh ${{pi.name}} (${{error.message}}). Existing matches are retained. Try again later or refresh with the Python script.`;
                            break;
                        }} finally {{ clearTimeout(timeout); }}
                        await new Promise(resolve => setTimeout(resolve, 150));
                    }}
                    const saved = saveStored('arxiv_orcid_works_v1', browserWorks);
                    updatePIStatus();
                    applyHighlightsAndRender();
                    message.textContent = `${{completed}} ORCID records refreshed. ` + failure +
                        (saved ? '' : ' Works cache could not be saved in this browser.') +
                        (piEditorDirty ? ' You still have unsaved watchlist changes.' : '');
                }} finally {{ refreshingPIs = false; button.disabled = false; }}
            }}

            function comparePaperElements(a, b) {{
                return Number(b.dataset.matchCount) - Number(a.dataset.matchCount)
                    || Number(b.dataset.piScore) - Number(a.dataset.piScore)
                    || Number(a.dataset.originalIndex) - Number(b.dataset.originalIndex);
            }}

            // --- HIGHLIGHTING & DYNAMIC SORTING ---
            function applyHighlightsAndRender() {{
                const sortedKw = activeKeywords.slice().sort((a, b) => b.length - a.length);
                let regex = null;
                if (sortedKw.length > 0) {{
                    const patternString = "\\\\b(" + sortedKw.map(escapeRegExp).join('|') + ")\\\\b";
                    regex = new RegExp(patternString, 'gi');
                }}

                allPapers.forEach(paper => {{
                    const span = paper.querySelector('.abstract-text');
                    const text = span.dataset.originalText;
                    
                    // Use a Set to track unique keyword matches
                    let matchedUniqueKeywords = new Set(); 

                    let highlighted = '';
                    let end = 0;
                    if (regex) {{
                        regex.lastIndex = 0;
                        for (const match of text.matchAll(regex)) {{
                            matchedUniqueKeywords.add(match[0].toLowerCase());
                            highlighted += escapeHTML(text.slice(end, match.index)) + `<span class="highlight">${{escapeHTML(match[0])}}</span>`;
                            end = match.index + match[0].length;
                        }}
                    }}
                    span.innerHTML = highlighted + escapeHTML(text.slice(end));
                    
                    // The total match count is now the number of unique keywords in the Set
                    let matchCount = matchedUniqueKeywords.size;
                    paper.dataset.matchCount = matchCount;
                    const record = currentDataset[Number(paper.dataset.originalIndex)];
                    const matches = getPIMatches(record);
                    const piScore = matches.reduce((sum, pi) => sum + pi.weight, 0);
                    paper.dataset.piScore = document.getElementById('orcidEnabled').checked ? piScore : 0;
                    record.keyword_matches = [...matchedUniqueKeywords];
                    record.keyword_score = matchCount;
                    record.orcid_matches = matches.map(pi => ({{name: pi.name, orcid: pi.orcid, priority: pi.priority, weight: pi.weight}}));
                    record.pi_score = piScore;
                    paper.querySelector('.orcid-matches').innerHTML = matches.length ? `PI score ${{piScore}}: ` + matches.map(pi =>
                        `<a href="https://orcid.org/${{pi.orcid}}" target="_blank" rel="noopener">${{escapeHTML(pi.name)}}</a> (${{escapeHTML(pi.priority)}}, +${{pi.weight}})`
                    ).join('; ') : '';
                    
                    const badge = paper.querySelector('.match-badge');
                    if (matchCount > 0) {{
                        badge.innerText = `${{matchCount}} Keyword${{matchCount > 1 ? 's' : ''}}`;
                        badge.style.display = 'block';
                    }} else {{
                        badge.style.display = 'none';
                    }}
                }});

                allPapers.sort(comparePaperElements);

                const paperList = document.getElementById('paperList');
                allPapers.forEach(paper => paperList.appendChild(paper));
                
                filterPapers(); 
            }}

            // --- SEARCH & PAGINATION ---
            function cacheCutoff(days, reference = new Date()) {{
                const date = new Date(reference);
                date.setUTCHours(0, 0, 0, 0);
                date.setUTCDate(date.getUTCDate() - days + 1);
                return date.toISOString().slice(0, 10);
            }}

            function updateCacheWindow() {{
                const input = document.getElementById('cacheDays');
                const days = Number(input.value);
                if (!Number.isInteger(days) || days < 7 || days > 28) {{
                    input.value = activeCacheDays;
                    document.getElementById('cacheWindowSummary').textContent = 'Choose a whole number from 7 to 28 days.';
                    return;
                }}
                activeCacheDays = days;
                saveStored('arxiv_cache_days_v1', days);
                filterPapers();
            }}

            function showCachedPapers() {{
                currentDataset = cachedDataset;
                datasetMode = 'cache';
                document.getElementById('cacheDays').disabled = false;
                rebuildDOM(cachedDataset);
            }}

            function renderPage() {{
                allPapers.forEach(p => p.style.display = 'none');
                
                const startIndex = (currentPage - 1) * papersPerPage;
                const endIndex = startIndex + papersPerPage;
                const papersToShow = filteredPapers.slice(startIndex, endIndex);
                
                papersToShow.forEach(p => p.style.display = 'block');
                
                if (window.MathJax) {{ MathJax.typesetPromise(); }}
                updatePaginationControls();
            }}

            function filterPapers() {{
                const query = document.getElementById('searchInput').value.toLowerCase();
                const cutoff = cacheCutoff(activeCacheDays);
                const today = new Date().toISOString().slice(0, 10);
                filteredPapers = allPapers.filter(paper => {{
                    const record = currentDataset[Number(paper.dataset.originalIndex)];
                    const inWindow = datasetMode !== 'cache' || (record.published >= cutoff && record.published <= today);
                    return inWindow && (!query || paper.innerText.toLowerCase().includes(query));
                }});
                document.getElementById('cacheWindowSummary').textContent = datasetMode === 'cache'
                    ? `Showing ${{cutoff}} through ${{today}} (UTC). Expand up to 28 days.`
                    : 'Live query results. Select Show local cache to return to the saved window.';
                currentPage = 1; 
                renderPage();
            }}

            function updatePaginationControls() {{
                const totalPages = Math.ceil(filteredPapers.length / papersPerPage) || 1;
                document.getElementById('pageInfo').innerText = `Page ${{currentPage}} of ${{totalPages}} (${{filteredPapers.length}} total)`;
                document.getElementById('prevBtn').disabled = currentPage === 1;
                document.getElementById('nextBtn').disabled = currentPage === totalPages;
            }}

            function prevPage() {{ if (currentPage > 1) {{ currentPage--; renderPage(); window.scrollTo(0, 0); }} }}
            function nextPage() {{ if (currentPage < Math.ceil(filteredPapers.length / papersPerPage)) {{ currentPage++; renderPage(); window.scrollTo(0, 0); }} }}
        </script>
    </body>
    </html>
    """
    
    with open(HTML_FILE, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"Successfully built the single-page application at: {HTML_FILE}")

def main():
    global BASE_DIR, CACHE_FILE, HTML_FILE
    parser = argparse.ArgumentParser(description="Automated arXiv Query Script")
    parser.add_argument('--dir', help='Base directory to save arXiv cache and HTML files.')
    parser.add_argument('--pi-tracker', help='Import PI names and priorities from an updated XLSX tracker.')
    parser.add_argument('--pi-config', help='Custom PI JSON watchlist (default: tracked_pis.json beside this script).')
    parser.add_argument('--refresh-orcid', action='store_true', help='Refresh ORCID public works even if cached within 24 hours.')
    parser.add_argument('--offline', action='store_true', help='Rebuild HTML using existing arXiv and ORCID caches only.')
    parser.add_argument('--cache-days', type=int, choices=range(7, 29), default=DEFAULT_CACHE_DAYS,
                        metavar='7..28', help='Initial HTML cache window in days (default: 14). Retains 28 days locally.')
    args = parser.parse_args()
    directory = args.dir or os.getenv('ARXIV_BASE_DIR')
    if not directory:
        directory = input("Enter the path to save arXiv data (or press Enter to use './arXiv_data'): ").strip() or './arXiv_data'
    BASE_DIR = os.path.abspath(os.path.expanduser(directory))
    CACHE_FILE = os.path.join(BASE_DIR, 'arxiv_cache.json')
    HTML_FILE = os.path.join(BASE_DIR, 'arxiv_homepage.html')
    try:
        people = load_watchlist(args.pi_config, args.pi_tracker)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    os.makedirs(BASE_DIR, exist_ok=True)
    # Persist explicit tracker/config choices for generated shortcut runs.
    settings_file = os.path.join(BASE_DIR, 'pi_settings.json')
    if args.pi_config or args.pi_tracker:
        with open(settings_file, 'w', encoding='utf-8') as output:
            json.dump({'people': people}, output, indent=2)
    elif os.path.exists(settings_file):
        people = load_watchlist(settings_file)
    if not args.offline:
        create_launcher_shortcut(BASE_DIR)
        paper_cache = fetch_and_cache_papers()
    else:
        with open(CACHE_FILE, encoding='utf-8') as source:
            paper_cache = json.load(source)
    index, statuses = build_orcid_index(people, os.path.join(BASE_DIR, 'orcid_works_cache.json'),
                                       refresh=args.refresh_orcid, offline=args.offline)
    for paper in paper_cache:
        paper['orcid_matches'] = match_paper(paper, people, index)
        paper['pi_score'] = sum(pi['weight'] for pi in paper['orcid_matches'])
    generate_single_html(paper_cache, people, index, statuses, cache_days=args.cache_days)


if __name__ == "__main__":
    main()
