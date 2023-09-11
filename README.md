A tool to retrieve, store, inspect and categorize your liked and bookmarked tweets.

⚠ Using this tool likely violates the Twitter Terms of Service! Do not use it! ⚠

I extensively used ChatGPT (GPT-4 with Advanced Data Analytics enabled) for this project. Prompts are in the `/ChatGPT` folder. I manually customized the script to support both bookmarks and likes.

Also see https://github.com/laszlovandenhoek/twitter-scraps-manager to see what you could do with this data.

# Requirements

- Python
- Postgres
  - Docker-compose supported
- Chromium-based browser

# Running

- Configure Postgres
  - edit password in `docker-compose.yml`
- Copy `db_config.ini.example` to `db_config.ini`
  - set `password` to same value
- Set up Virtual environment
  - `python3 -m venv .env`
  - `. ./.env/activate`
  - `pip install -r requirements.txt`
- Log in to Twitter in your browser
  - Open Developer Console (F12)
  - Open network tab
  - Check "Preserve log"
  - Filter Fetch/XHR requests containing "graphql"
  - Navigate to to `https://twitter.com/<username>/likes`
  - Navigate to to `https://twitter.com/i/bookmarks`
  - In the list of requests, right-click the "Likes?..." request and select "Copy as Node.js fetch"
    - Paste this somewhere
  - Do the same for the "Bookmarks?" request
    - Paste that too
- Run the script
  - Command line should be: `python3 /path/to/getbookmarks.py 'fetch(...);' 'fetch(...);'`, where the `fetch(...)` parts are the ones previously copy/pasted from the network requests tab
    - Note the single quotes around the fetch commands; this is to prevent command interpolation by the shell.

Let the script run until it ends naturally. At that point, you will have a database full of tweets. If you add more likes/bookmarks later, you can rerun the script, but each time you do, let it run without interruptions, or you'll have gaps. If that does happen anyway for some reason, remove all tweets with a higher sortIndex than the first missing tweet (or just drop the whole database) and rerun the script. 