import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import json
import os
import re
from datetime import datetime, timedelta

# === FILE PATHS ===
BASE_DIR = '/home/daniel037bee/Desktop/Labwork/arXiv'
os.makedirs(BASE_DIR, exist_ok=True)

CACHE_FILE = os.path.join(BASE_DIR, 'arxiv_cache.json')
HTML_FILE = os.path.join(BASE_DIR, 'arxiv_homepage.html')

DEFAULT_KEYWORDS = [
    "dark matter", 
    "black hole", 
    "simulation", 
    "machine learning",
    "milky way"
]

def fetch_and_cache_papers():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            cache = json.load(f)
    else:
        cache = []
        
    # --- NEW: Erase papers older than 7 days from the cache ---
    cutoff_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    original_count = len(cache)
    cache = [paper for paper in cache if paper['published'] >= cutoff_date]
    if len(cache) < original_count:
        print(f"Purged {original_count - len(cache)} old papers from the local cache.")

    known_ids = {paper['id'] for paper in cache}
    
    query = 'cat:astro-ph.GA IGNORE cat:astro-ph.CO IGNORE cat:astro-ph.SR ANDNOT cat:astro-ph.EP'
    search_query = urllib.parse.quote(query)
    url = f"http://export.arxiv.org/api/query?search_query={search_query}&start=0&max_results=50&sortBy=submittedDate&sortOrder=descending"
    
    print("Checking arXiv for new papers...")
    
    try:
        response = urllib.request.urlopen(url)
        xml_data = response.read()
        root = ET.fromstring(xml_data)
        namespace = {'atom': 'http://www.w3.org/2005/Atom'}
        entries = root.findall('atom:entry', namespace)
        
        new_papers = []
        for entry in entries:
            paper_id = entry.find('atom:id', namespace).text
            if paper_id in known_ids:
                continue 
                
            title = entry.find('atom:title', namespace).text.replace('\n', ' ').strip()
            summary = entry.find('atom:summary', namespace).text.replace('\n', ' ').strip()
            authors = [author.find('atom:name', namespace).text for author in entry.findall('atom:author', namespace)]
            published = entry.find('atom:published', namespace).text
            
            # Only add the paper if it meets our 7-day cutoff (arXiv sometimes bumps old papers)
            if published[:10] >= cutoff_date:
                new_papers.append({
                    'id': paper_id,
                    'title': title,
                    'abstract': summary,
                    'authors': ', '.join(authors),
                    'published': published[:10] 
                })
            
        if new_papers:
            print(f"Found {len(new_papers)} new papers! Updating cache...")
            cache.extend(new_papers)
            
        # Always sort and save, even if no new papers, to ensure the old ones were purged
        cache.sort(key=lambda x: x['published'], reverse=True)
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, indent=4)
                
        return cache
        
    except Exception as e:
        print(f"An error occurred: {e}")
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
                <div class="date">Published: {paper['published']}</div>
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
                document.getElementById('loadingIndicator').style.display = 'block';
                
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

                const arxivUrl = `http://export.arxiv.org/api/query?search_query=${{encodeURIComponent(queryStr)}}&start=0&max_results=50&sortBy=submittedDate&sortOrder=descending`;
                const proxyUrl = `https://api.allorigins.win/raw?url=${{encodeURIComponent(arxivUrl)}}`;
                
                try {{
                    const response = await fetch(proxyUrl);
                    const str = await response.text();
                    const data = new window.DOMParser().parseFromString(str, "text/xml");
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
                    alert("Network error. Try again later. " + e);
                }}
                document.getElementById('loadingIndicator').style.display = 'none';
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
                        <div class="date">Published: ${{paper.published}}</div>
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
