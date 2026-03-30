
Astrophysics arXiv Daily Dashboard - User Guide & Documentation
================================================================================

This document covers the initial setup, automation techniques, and usage guide 
for the Automated arXiv Query Script (v0.2.0).

--------------------------------------------------------------------------------
1. INSTALLATION & THE AUTO-GENERATED SHORTCUT (.bat / .sh)
--------------------------------------------------------------------------------

[ Initial Setup ]
To use the tool for the first time, you need to run the Python script manually 
so it can build your local cache and generate your shortcut files.

For Windows (using PowerShell):
1. Open PowerShell.
2. Navigate to your script's folder:
   cd "D:\User\부산대학교\Lab\arXiv"
3. Run the script:
   python arXiv_query_automated_v0.2.0.py

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
   last 7 days. These are loaded locally from your `arxiv_cache.json` file, 
   making the webpage lightning-fast. MathJax is embedded, so all LaTeX math 
   formulas in titles and abstracts will render perfectly.

2. Keyword Highlights & Dynamic Sorting
   - Use the "Manage Highlights & Sorting" panel to add custom keywords.
   - When a keyword is added, the dashboard instantly scans all loaded abstracts.
   - Matches are highlighted in bright yellow/orange.
   - Crucially, the papers are instantly re-sorted: papers with the highest 
     number of unique keyword matches are pushed to the very top of the list.
   - Your keywords are saved locally in your browser, so they will still be 
     there the next time you open the HTML file.

3. Live arXiv Query Builder (API Fetching)
   If you want to search beyond your 7-day cache or look at different sub-fields:
   - Use the dropdown menus to Include, Exclude, or Ignore specific sub-categories 
     (e.g., Include Cosmology [CO], Exclude Earth/Planets [EP]).
   - Click "Go (Live Fetch)". 
   - The webpage will reach out to the arXiv API in real-time, bypassing your 
     local cache, and fetch up to 500 historical papers matching your rules. 
   - It respects rate-limits automatically to prevent arXiv from blocking you.

4. Download Custom Results
   If you use the Live Query to fetch a highly specific list of papers, you can 
   click "Download Results" to save that exact view as a raw `.json` file for 
   later data analysis or record-keeping.

5. Real-Time Search & Pagination
   The search bar instantly filters papers as you type. It checks titles, 
   authors, and abstracts simultaneously. Papers are cleanly paginated (50 per 
   page) to keep the browser running smoothly.


Please report me about any issues or ideas for this script at:
daniel037bee@pusan.ac.kr

Thx!
