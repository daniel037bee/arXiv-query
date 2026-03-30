import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
import json
import os
import re
import time
from datetime import datetime, timedelta
import argparse
import sys
import stat

def create_launcher_shortcut(base_dir):
    """Automatically creates a .bat or .sh file to launch the script in the future."""
    # Get the directory where this python script lives and its exact filename
    script_dir = os.path.dirname(os.path.abspath(__file__))
    script_name = os.path.basename(__file__)
    
    if os.name == 'nt':  # Windows
        bat_path = os.path.join(script_dir, 'Run_arXiv_Query.bat')
        if not os.path.exists(bat_path):
            print(f"Creating Windows launcher at: {bat_path}")
            with open(bat_path, 'w') as f:
                f.write("@echo off\n")
                f.write("echo Running arXiv Query Script...\n")
                f.write('cd /d "%~dp0"\n')
                # Use sys.executable to ensure it uses the exact same Python environment
                f.write(f'"{sys.executable}" "{script_name}" --dir "{base_dir}"\n')
                f.write("pause\n")
                
    else:  # macOS / Linux
        sh_path = os.path.join(script_dir, 'run_arxiv_query.sh')
        if not os.path.exists(sh_path):
            print(f"Creating Mac/Linux launcher at: {sh_path}")
            with open(sh_path, 'w') as f:
                f.write("#!/bin/bash\n")
                f.write('echo "Running arXiv Query Script..."\n')
                f.write('cd "$(dirname "$0")"\n')
                f.write(f'"{sys.executable}" "{script_name}" --dir "{base_dir}"\n')
            
            # Make the .sh file executable
            st = os.stat(sh_path)
            os.chmod(sh_path, st.st_mode | stat.S_IEXEC)

# Set up argument parsing
parser = argparse.ArgumentParser(description="Automated arXiv Query Script")
parser.add_argument(
    '--dir', 
    type=str, 
    default=None,  # Default to None so we can check if it was used
    help='Base directory to save arXiv cache and HTML files.'
)
args = parser.parse_args()

# Determine BASE_DIR based on the 3-tier fallback
if args.dir:
    # 1. Use command-line argument if provided
    BASE_DIR = args.dir
elif os.getenv('ARXIV_BASE_DIR'):
    # 2. Use environment variable if provided
    BASE_DIR = os.getenv('ARXIV_BASE_DIR')
else:
    # 3. Interactive prompt if nothing was provided
    print("No directory specified via command line or environment variable.")
    user_input = input("Enter the path to save arXiv data (or press Enter to use './arXiv_data'): ").strip()
    # Use the user's input, or default to './arXiv_data' if they just pressed Enter
    BASE_DIR = user_input if user_input else './arXiv_data'

os.makedirs(BASE_DIR, exist_ok=True)

# Generate the automated launcher shortcut based on the resolved directory
create_launcher_shortcut(BASE_DIR)

CACHE_FILE = os.path.join(BASE_DIR, 'arxiv_cache.json')
HTML_FILE = os.path.join(BASE_DIR, 'arxiv_homepage.html')

DEFAULT_KEYWORDS = [
    "obscured",
    "active galactic nuclei", 
    "early universe", 
    "early times", 
    "kerr",
    "black hole"
]

def fetch_oai_pmh_papers(cutoff_date):
    """
    Harvests metadata from the arXiv OAI-PMH endpoint.
    Downloads the entire 'physics:astro-ph' set from the cutoff date, 
    then filters for GA (and excludes EP) client-side.
    """
    base_url = "http://export.arxiv.org/oai2"
    
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
    
    while True:
        query_string = urllib.parse.urlencode(params)
        url = f"{base_url}?{query_string}"
        print(f"Harvesting OAI-PMH page: {url}")
        
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) arXiv-Dashboard-Bot/1.0'})
        
        try:
            response = urllib.request.urlopen(req)
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

        root = ET.fromstring(xml_data)
        
        # Check for OAI-level errors (e.g., noRecordsMatch)
        error = root.find('oai:error', ns)
        if error is not None:
            if error.attrib.get('code') == 'noRecordsMatch':
                print("No new records found for this date range.")
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
                title = metadata.find('arxiv:title', ns).text.replace('\n', ' ').strip()
                abstract = metadata.find('arxiv:abstract', ns).text.replace('\n', ' ').strip()
                
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
                    'published': published
                })

        # Check for pagination (resumptionToken)
        token_element = root.find('.//oai:resumptionToken', ns)
        if token_element is not None and token_element.text:
            token = token_element.text
            # When using a resumption token, all other params MUST be omitted
            params = {'verb': 'ListRecords', 'resumptionToken': token} 
            time.sleep(5)  # Mandatory courtesy delay between pages
        else:
            break  # No more pages

    return new_papers

def fetch_and_cache_papers():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            cache = json.load(f)
    else:
        cache = []
        
    # --- Erase papers older than 7 days from the cache ---
    cutoff_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    original_count = len(cache)
    cache = [paper for paper in cache if paper['published'] >= cutoff_date]
    if len(cache) < original_count:
        print(f"Purged {original_count - len(cache)} old papers from the local cache.")

    known_ids = {paper['id'] for paper in cache}
    
    print(f"Checking arXiv OAI-PMH for new astro-ph papers since {cutoff_date}...")
    harvested_papers = fetch_oai_pmh_papers(cutoff_date)
    
    # Filter against papers we already have in the cache
    new_papers = [p for p in harvested_papers if p['id'] not in known_ids]
    
    if new_papers:
        print(f"Found {len(new_papers)} new GA papers! Updating cache...")
        cache.extend(new_papers)
        
    # Sort and save
    cache.sort(key=lambda x: x['published'], reverse=True)
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=4)
            
    return cache

def generate_single_html(cache):
    print("Generating the single-page application...")
    
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
            <div class="last-updated">Local Cache: {len(cache)} papers (7-day window)<br>Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
        </div>
        
        <div class="control-panel">
            <div class="panel-box keyword-box">
                <h3>Manage Highlights & Sorting</h3>
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

        <div class="search-container">
            <input type="text" id="searchInput" placeholder="Search authors, abstracts, or titles..." onkeyup="filterPapers()">
        </div>

        <div id="paperList">
    """
    
    for index, paper in enumerate(cache):
        html_content += f"""
            <div class="paper" data-original-index="{index}">
                <div class="match-badge">0 Matches</div>
                <h2><a href="{paper['id']}" target="_blank">{paper['title']}</a></h2>
                <div class="date">Announced: {paper['published']}</div>
                <div class="authors">{paper['authors']}</div>
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
            let currentDataset = {json.dumps(cache)}; 
            
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

                const storedKw = localStorage.getItem('arxiv_keywords');
                if (storedKw) {{ activeKeywords = JSON.parse(storedKw); }} 
                else {{ activeKeywords = defaultKeywords; }}

                renderKeywordUI();
                applyHighlightsAndRender();
                
                document.getElementById("newKeywordInput").addEventListener("keyup", function(event) {{
                    if (event.key === "Enter") addKeyword();
                }});
            }};

            // --- DOWNLOADING DATA ---
            function downloadResults() {{
                // Create a JSON blob of the current dataset
                const dataStr = JSON.stringify(currentDataset, null, 4);
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

                const arxivUrl = `http://export.arxiv.org/api/query?search_query=${{encodeURIComponent(queryStr)}}&start=0&max_results=500&sortBy=submittedDate&sortOrder=descending`;
                const proxyUrl = `https://api.allorigins.win/raw?url=${{encodeURIComponent(arxivUrl)}}`;
                
                let maxRetries = 3;
                let attempt = 0;
                let xmlStr = null;
                
                while (attempt < maxRetries) {{
                    try {{
                        const response = await fetch(proxyUrl);
                        
                        // Handle HTTP-level rate limiting from the proxy/origin
                        if (response.status === 429 || response.status === 503) {{
                            attempt++;
                            let waitTime = attempt * 10;
                            loadingInd.innerText = `Rate limited by arXiv (HTTP ${{response.status}}). Retrying in ${{waitTime}}s...`;
                            await new Promise(r => setTimeout(r, waitTime * 1000));
                            continue;
                        }}
                        if (!response.ok) throw new Error(`HTTP error! status: ${{response.status}}`);
                        
                        xmlStr = await response.text();
                        
                        // Handle cases where AllOrigins returns 200 OK, but the text is an arXiv HTML error page
                        if (xmlStr.includes('Retry-After') || xmlStr.includes('Too Many Requests') || !xmlStr.trim().startsWith('<')) {{
                            attempt++;
                            let waitTime = attempt * 10;
                            loadingInd.innerText = `ArXiv rate limit detected in payload. Retrying in ${{waitTime}}s...`;
                            await new Promise(r => setTimeout(r, waitTime * 1000));
                            continue;
                        }}
                        
                        break; // Success! Break out of the retry loop.
                        
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
                        
                        newPapers.push({{id, title, abstract, published, authors}});
                    }});
                    
                    if(newPapers.length === 0) {{ alert("Query successful, but no papers matched those exact rules."); }}
                    else {{ 
                        currentDataset = newPapers; // Update the global dataset for the download button
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
                        <h2><a href="${{paper.id}}" target="_blank">${{paper.title}}</a></h2>
                        <div class="date">Announced: ${{paper.published}}</div>
                        <div class="authors">${{paper.authors}}</div>
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
                    badge.innerHTML = `${{kw}} <span class="remove-kw" onclick="removeKeyword('${{kw}}')" title="Remove">&times;</span>`;
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
                    let text = span.dataset.originalText;
                    
                    // Use a Set to track unique keyword matches
                    let matchedUniqueKeywords = new Set(); 

                    if (regex) {{
                        text = text.replace(regex, (match, p1) => {{
                            // Add the lowercased match to the Set
                            matchedUniqueKeywords.add(p1.toLowerCase()); 
                            return `<span class="highlight">${{p1}}</span>`;
                        }});
                    }}
                    span.innerHTML = text;
                    
                    // The total match count is now the number of unique keywords in the Set
                    let matchCount = matchedUniqueKeywords.size;
                    paper.dataset.matchCount = matchCount;
                    
                    const badge = paper.querySelector('.match-badge');
                    if (matchCount > 0) {{
                        badge.innerText = `${{matchCount}} Hit${{matchCount > 1 ? 's' : ''}}`;
                        badge.style.display = 'block';
                    }} else {{
                        badge.style.display = 'none';
                    }}
                }});

                allPapers.sort((a, b) => {{
                    const countA = parseInt(a.dataset.matchCount);
                    const countB = parseInt(b.dataset.matchCount);
                    if (countB !== countA) return countB - countA;
                    return parseInt(a.dataset.originalIndex) - parseInt(b.dataset.originalIndex);
                }});

                const paperList = document.getElementById('paperList');
                allPapers.forEach(paper => paperList.appendChild(paper));
                
                filterPapers(); 
            }}

            // --- SEARCH & PAGINATION ---
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
                if (query === '') {{
                    filteredPapers = allPapers;
                }} else {{
                    filteredPapers = allPapers.filter(paper => paper.innerText.toLowerCase().includes(query));
                }}
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

if __name__ == "__main__":
    paper_cache = fetch_and_cache_papers()
    if paper_cache:
        generate_single_html(paper_cache)
