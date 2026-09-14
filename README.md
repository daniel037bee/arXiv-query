
Astrophysics arXiv Daily Dashboard - User Guide & Documentation
================================================================================

This document covers the initial setup, automation techniques, and usage guide 
for the Automated arXiv Query Script.

--------------------------------------------------------------------------------
1. INSTALLATION & THE AUTO-GENERATED SHORTCUT (.bat / .sh)
--------------------------------------------------------------------------------

[ Initial Setup ]
To use the tool for the first time, you need to run the Python script manually 
so it can build your local cache and generate your shortcut files.

For Windows (using PowerShell):
1. Open PowerShell.
2. Navigate to your script's folder:
   cd "Drive:\Your\Path\to\.py\file"
3. Run the script:
   python arXiv_query_automated_v(version).py

During this first run, the script will ask where you want to save your arXiv 
data (the JSON cache and HTML file). Press Enter to use the default folder, or 
type a specific path. 

[ The Auto-Shortcut Maker ]
Once the script successfully determines your save directory, it detects your 
Operating System and automatically writes a launcher file in the exact same 
folder as your Python script:
- Windows: Run_arXiv_Query.bat
- macOS/Linux: run_arxiv_query.sh

How it works:
The script bakes your specific Python executable path, the script name, and your 
chosen save directory right into the shortcut file. 
From now on, you NEVER have to use the terminal to run the tool. You simply 
double-click the generated `.bat` or `.sh` file to fetch the latest papers.

--------------------------------------------------------------------------------
2. HOW TO AUTOMATE THE SCRIPT (RUN EVERY DAY)
--------------------------------------------------------------------------------
You can configure your computer to run the generated shortcut file automatically 
in the background every day (e.g., every morning at 8:00 AM) so your HTML 
dashboard is always up to date when you sit down at your desk.

[ Windows: Using Task Scheduler ]
1. Press the Windows Key, type "Task Scheduler", and hit Enter.
2. In the right-hand panel, click "Create Basic Task...".
3. Name it something like "ArXiv Daily Fetch" and click Next.
4. Trigger: Choose "Daily" -> set your preferred time (e.g., 8:00 AM).
5. Action: Choose "Start a program".
6. Program/script: Click "Browse..." and select your newly generated 
   `Run_arXiv_Query.bat` file in your arXiv folder.
7. Click Next, then Finish. Windows will now run it automatically every day.

[ macOS / Linux: Using Cron ]
1. Open your Terminal.
2. Type `crontab -e` and press Enter to edit your cron jobs.
3. Add a line to run the script every day at 8:00 AM:
   0 8 * * * /path/to/your/folder/run_arxiv_query.sh
4. Save and exit the editor. 

--------------------------------------------------------------------------------
3. EXPLANATION & USAGE OF THE .HTML DASHBOARD
--------------------------------------------------------------------------------

Once the script finishes fetching papers, it generates a Single-Page Application 
(SPA) named `arxiv_homepage.html` in your designated data folder. Open this 
file in any web browser (Chrome, Edge, Safari, Firefox). 

[ Core Features ]

1. Local Cache Viewer (The Default View)
   By default, the page displays astrophysics papers announced on arXiv over the 
   last 14 days. Change "Cache window" to any whole number from 7 to 28 days.
   The Python script retains 28 days in `arxiv_cache.json`, so expanding the
   displayed window needs no new download. The first run after upgrading
   backfills this larger window. Dates include today and use UTC boundaries.
   Your chosen display window is remembered in the same browser.
   These papers are embedded in the generated HTML file,
   making the webpage lightning-fast. MathJax is embedded, so all LaTeX math 
   formulas in titles and abstracts will render perfectly.

2. Keyword Highlights & Dynamic Sorting
   - Use the "Keywords (primary ranking)" panel to add custom keywords.
   - When a keyword is added, the dashboard instantly scans all loaded abstracts.
   - Matches are highlighted in bright yellow/orange.
   - Crucially, the papers are instantly re-sorted: papers with the highest 
     number of unique keyword matches are pushed to the very top of the list.
   - Your keywords are saved locally in your browser, so they will still be 
     there the next time you open the HTML file.

3. Live arXiv Query Builder (API Fetching)
   If you want to search beyond your cache or look at different sub-fields:
   - Use the dropdown menus to Include, Exclude, or Ignore specific sub-categories 
     (e.g., Include Cosmology [CO], Exclude Earth/Planets [EP]).
   - Click "Go (Live Fetch)". 
   - The webpage will reach out to the arXiv API in real-time, bypassing your 
     local cache, and fetch up to 500 historical papers matching your rules. 
   - It respects rate-limits automatically to prevent arXiv from blocking you.
   - "Show local cache" returns to your saved papers and selected date window.
     The cache-window control does not restrict live query results.

4. Download Custom Results
   If you use the Live Query to fetch a highly specific list of papers, you can 
   click "Download Results" to save the date/search-filtered results across all
   pages, in displayed order, as a raw `.json` file for
   later data analysis or record-keeping.

5. Real-Time Search & Pagination
   The search bar instantly filters papers as you type. It checks titles, 
   authors, and abstracts simultaneously. Papers are cleanly paginated (50 per 
   page) to keep the browser running smoothly.

6. Editable PI ORCID Tracking (Secondary Ranking)
   - Expand "PI ORCID tracking (secondary)" to edit the list. This section is
     folded by default. Add/remove PIs, edit their ORCID iDs or URLs, and choose
     their priorities. Click "Save changes" to update the ranking and remember
     your list in the same browser. Invalid and duplicate ORCIDs are rejected.
   - Defaults come from `grad_outreach_tracker_v7.xlsx`, Outreach Tracker,
     rows 4–140: 145 named researchers after splitting multi-PI rows.
     There are 121 active PIs and 24 Skip entries. The workbook has no ORCID
     column, so the bundled `tracked_pis.json` records separately verified IDs
     and their sources. The original workbook is not modified or needed to run.
   - Weights are Highest = 3, High = 2, Medium = 1, Skip (sim/theory) = 0.
     PI score is the sum of the weights of the unique matched PIs that the paper
     promotes. Skip entries receive no boost; they do not exclude otherwise
     relevant papers.
   - A tracked PI promotes a paper only when they are its first, second or
     corresponding author. arXiv publishes no corresponding-author field, so
     "corresponding" means the arXivRaw submitter or a contact address printed
     in the record's comments or abstract. Any other position, last author
     included, is still listed under the paper but marked "no boost" and adds
     nothing to the PI score. Edit PROMOTED_ROLES in `arxiv_orcid.py` to change
     the rule; adding "last" also promotes senior authors.
   - Submitters are looked up one paper at a time through the OAI `arXivRaw`
     format, only for papers whose tracked PI is otherwise unpromoted, and at
     most 40 per run (`SUBMITTER_LOOKUP_LIMIT`). The rest resolve on later runs,
     each result is cached with the paper, and `--offline` skips the lookups.
     Live browser queries use the Atom API, which carries no submitter, so there
     corresponding authorship rests on printed addresses alone.
   - Sorting is strictly: unique abstract keyword count descending, then PI
     score descending, then original order. A PI-only match never outranks a
     paper with a keyword hit. Several PIs can contribute to the secondary score.
   - Matches use exact author ORCID metadata when available, or the paper's
     exact arXiv ID / DOI in a tracked PI's public ORCID works. There is no
     author-name fallback for identifying a PI. Names are used only to place a
     PI who is already identified by ORCID (surname plus first initial) when
     arXiv received no author ORCIDs for that paper, and to compare a PI against
     the submitter. arXiv versions are normalized and PIs count once even when
     more than one identifier matches. Category selection is unchanged.
   - Author positions are stored per paper, so an existing `arxiv_cache.json`
     from an earlier version is re-harvested once over the full 28-day window.
   - Public ORCID works are cached for 24 hours. "Save changes" fetches works for
     newly enabled/added IDs as needed; "Refresh ORCID works" forces an update.
     API failure preserves previous evidence and shows a message. Empty or
     private/incomplete ORCID work lists can miss papers; the editor reports
     records with no public identifiers. During verification, 11 active PIs
     had no usable public work identifiers despite having a valid ORCID.
   - "Export watchlist" downloads your saved settings as JSON. Use that file
     with `--pi-config` to apply browser edits to future Python runs. Browser
     changes do not silently overwrite the workbook or files on disk.
   - "Reset to tracker defaults" restores the list embedded by the Python run.
     An already saved browser list takes precedence when regenerating HTML.
     If browser storage is unavailable, changes work for the current session;
     export the watchlist to preserve it.

[ Command-line options ]
Keep `arxiv_orcid.py` and `tracked_pis.json` alongside the main Python script.
All runtime code uses the Python standard library; no Excel packages are needed.

Normal run (14-day initial display; 28-day retention):
  python arXiv_query_automated_v0.4.1.py --dir "./arXiv_data"

Choose a different initial display window (a saved browser choice takes precedence):
  python arXiv_query_automated_v0.4.1.py --dir "./arXiv_data" --cache-days 21

Use a watchlist exported from the page:
  python arXiv_query_automated_v0.4.1.py --dir "./arXiv_data" --pi-config "path/to/tracked_pis.json"

Import names/priorities from an updated tracker (existing verified names retain IDs):
  python arXiv_query_automated_v0.4.1.py --dir "./arXiv_data" --pi-tracker "path/to/tracker.xlsx"
The tracker must contain `PI (contact)` and `Priority` headers. Optional ORCID and
Email columns are accepted, with one PI per row when either is supplied. An email
is never used to identify a PI, only to confirm a corresponding author already
matched by ORCID.
New names without IDs remain unresolved until an ID is provided. Imported/custom
settings are saved as `pi_settings.json` in the data folder for later shortcut runs.
After an import, use the page's reset button to replace an older browser watchlist.

Force an ORCID refresh, or rebuild from existing caches without network access:
  python arXiv_query_automated_v0.4.1.py --dir "./arXiv_data" --refresh-orcid
  python arXiv_query_automated_v0.4.1.py --dir "./arXiv_data" --offline

If ORCID requires authentication, the Python fetcher accepts an optional
`ORCID_ACCESS_TOKEN` environment variable. It is never embedded in the HTML.
ORCID API reference: https://info.orcid.org/documentation/api-tutorials/api-tutorial-read-data-on-a-record/

[ Tests ]
  python -m unittest discover -s tests -v
Node.js is used, when available, to test the JavaScript embedded in generated HTML.
Set `ARXIV_TEST_NODE` to its executable path if it is not on PATH.

Please report me about any issues or ideas for this script at:
daniel037bee@pusan.ac.kr

Thx!
