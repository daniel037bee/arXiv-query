Configuration & Usage

The script requires a directory to save the downloaded arXiv data and the generated HTML dashboard. You can run the script interactively, or automate it by pre-configuring the save path to bypass the prompts.




--- Method 1: Interactive Prompt (Basic Usage) ---

If you simply run the script from your terminal, it will pause and ask you where you want to save the files. This is the easiest way to use the script manually.

Command:
python arXiv_query_automated_v0.2.0.py

Prompt:
Enter the path to save arXiv data (or press Enter to use './arXiv_data'):

You can type your desired absolute or relative path, or simply press Enter to let the script create a default "arXiv_data" folder in your current directory.


# If you want to hard-code the path:

# Permanently set the directory
BASE_DIR = '/PATH/YOU/WANT/'  
os.makedirs(BASE_DIR, exist_ok=True)




--- Method 2: Console & Automation (Bypassing the Prompt) ---

If you want to run the script automatically in the background (e.g., via a cron job, WSL, or Windows Task Scheduler) without it getting stuck waiting for user input, you must provide the path beforehand. You can do this in two ways:

Option A: Command-Line Argument (--dir)
Pass the directory path directly using the --dir flag. This is ideal for fast terminal runs and one-off overrides.

Command:
python arXiv_query_automated_v0.2.0.py --dir /path/to/your/custom/folder


Option B: Environment Variables
Set the ARXIV_BASE_DIR environment variable. The script will detect this variable in the background and use it automatically every time it runs.

1. Linux / macOS / WSL:
   export ARXIV_BASE_DIR="/path/to/your/custom/folder"
   python arXiv_query_automated_v0.2.0.py

2. Windows (Command Prompt):
   set ARXIV_BASE_DIR=C:\path\to\your\custom\folder
   python arXiv_query_automated_v0.2.0.py

3. Windows (PowerShell):
   $env:ARXIV_BASE_DIR="C:\path\to\your\custom\folder"
   python arXiv_query_automated_v0.2.0.py
