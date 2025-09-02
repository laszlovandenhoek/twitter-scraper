import base64
import json
import random
import sys
import json
from urllib.parse import quote
import urllib.parse

import psycopg2
import requests

import configparser


def initialize_database():
    # Initialize the parser and read the configuration file
    config = configparser.ConfigParser()
    config.read('db_config.ini')

    # Connect to the PostgreSQL server
    conn = psycopg2.connect(
        host=config['postgresql']['host'],
        dbname=config['postgresql']['dbname'],
        user=config['postgresql']['user'],
        password=config['postgresql']['password']
    )

    c = conn.cursor()

    # Create tables if they don't yet exist
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS tweet_index (
            rest_id VARCHAR(20) PRIMARY KEY,
            sort_index VARCHAR(20) NOT NULL,
            user_id TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            fetched_at TIMESTAMP NOT NULL DEFAULT NOW(),
            bookmarked BOOLEAN NOT NULL DEFAULT False,
            liked BOOLEAN NOT NULL DEFAULT False,
            important BOOLEAN NOT NULL DEFAULT False,
            archived BOOLEAN NOT NULL DEFAULT False,
            expanded BOOLEAN NOT NULL DEFAULT False,
            source_json JSONB NOT NULL
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS tweets (
            anchor_rest_id VARCHAR(20) NOT NULL,
            rest_id VARCHAR(20) NOT NULL,
            sort_index VARCHAR(20) NOT NULL,
            user_id TEXT NOT NULL,
            screen_name TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            fetched_at TIMESTAMP NOT NULL DEFAULT NOW(),
            full_text TEXT NOT NULL,
            bookmarked BOOLEAN NOT NULL DEFAULT False,
            liked BOOLEAN NOT NULL DEFAULT False,
            first_in_thread BOOLEAN NOT NULL DEFAULT False,
            last_in_thread BOOLEAN NOT NULL DEFAULT False,
            source_json JSONB NOT NULL,
            PRIMARY KEY (anchor_rest_id, rest_id),
            FOREIGN KEY (anchor_rest_id) REFERENCES tweet_index(rest_id)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS categories (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS tweet_categories (
            tweet_id VARCHAR(20),
            category_id INTEGER,
            PRIMARY KEY (tweet_id, category_id),
            FOREIGN KEY (tweet_id) REFERENCES tweets(rest_id),
            FOREIGN KEY (category_id) REFERENCES categories(id)
        )
    ''')

    conn.commit()

    print("database initialized")
    return conn


def save_tweets_to_database_index(conn, items: list[tuple[str, str, str, str, str, str, str, str]]):
    c = conn.cursor()
    # Check if we have data
    if not items:
        return True

    # Check if we have been here before
    for item in items:
        rest_id = int(item[0])

        # this will skip over tweets that were included as part of a different list
        c.execute(f"SELECT COUNT(rest_id) FROM tweet_index WHERE rest_id = '%s' AND ((bookmarked != liked) AND source_json IS NOT NULL)", (rest_id,))
        row = c.fetchone()

        if row[0] > 0:
            print(f"found existing record for rest_id {rest_id}")
            return True

        # New data, insert it
        c.execute(
            "INSERT INTO tweet_index (rest_id, sort_index, user_id, screen_name, created_at, bookmarked, liked, source_json) VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (rest_id) DO UPDATE SET bookmarked = EXCLUDED.bookmarked, liked = EXCLUDED.liked, source_json = EXCLUDED.source_json",
            item)
        conn.commit()
        print(f"added rest_id {rest_id}")

    return False

def expand_tweets(tweet_fetch_command):
    c = connection.cursor()
    c.execute("SELECT rest_id, user_id FROM tweet_index WHERE expanded = False")
    rows = c.fetchall()
    for row in rows:
        anchor_rest_id = row[0]
        anchor_user_id = row[1]
        print(f"expanding tweet {anchor_rest_id} by {anchor_user_id}")

        url, headers = tweet_fetch_command[6:-2].split(',', 1)
        unquoted_url = url.strip().strip('"')
        
        # Parse URL and update focalTweetId parameter
        url_parts = unquoted_url.split('?', 1)
        base_url = url_parts[0]
        params = url_parts[1]
        
        # Parse the variables parameter which contains focalTweetId
        param_parts = params.split('&')
        new_params = []
        for part in param_parts:
            variables_prefix = 'variables='
            if part.startswith(variables_prefix):
                # Decode the JSON-like variables string
                variables = json.loads(urllib.parse.unquote(part[len(variables_prefix):]))
                # Update focalTweetId
                variables['focalTweetId'] = anchor_rest_id
                # Re-encode the variables
                new_params.append(variables_prefix + urllib.parse.quote(json.dumps(variables)))
            else:
                new_params.append(part)
                
        url_with_id = base_url + '?' + '&'.join(new_params)
        print(f"url_with_id: {url_with_id}")
        headers = json.loads(headers.strip())
        
        # Fetch the tweet thread
        tweet_thread_json = fetch_data(url_with_id, headers['headers'])
        
        # Find the TimelineAddEntries instruction
        instructions = tweet_thread_json['data']['threaded_conversation_with_injections_v2']['instructions']
        entries = None
        for instruction in instructions:
            if instruction['type'] == 'TimelineAddEntries':
                entries = instruction['entries']
                break
                
        if entries is None:
            print(f"No TimelineAddEntries instruction found for tweet {anchor_rest_id}")
            continue
        
        for entry in entries:
            if 'content' not in entry:
                continue
                
            # Handle both direct itemContent and items array cases
            item_contents = []
            if 'itemContent' in entry['content']:
                item_contents.append(entry['content']['itemContent'])
            elif 'items' in entry['content']:
                for item in entry['content']['items']:
                    if 'item' in item and 'itemContent' in item['item']:
                        item_contents.append(item['item']['itemContent'])
            
            for item_content in item_contents:
                if 'tweet_results' not in item_content:
                    continue
                    
                tweet = item_content['tweet_results']['result']
                if tweet['__typename'] == 'TweetWithVisibilityResults':
                    tweet = tweet['tweet']
                    
                rest_id = tweet['rest_id']
                sort_index = entry['sortIndex']
                user_id = tweet['core']['user_results']['result']['rest_id']
                screen_name = tweet['core']['user_results']['result']['legacy']['screen_name']
                created_at = tweet['legacy']['created_at']
                full_text = tweet['legacy']['full_text']
                bookmarked = tweet['legacy'].get('bookmarked', False)
                liked = tweet['legacy'].get('favorited', False)
                
                if user_id != anchor_user_id:
                    print(f"skipping tweet {rest_id} by {user_id} because it's not by {anchor_user_id}")
                    continue
                
                # Determine position in thread
                first_in_thread = entry == entries[0]
                last_in_thread = entry == entries[-1]
                
                # Convert tweet to JSON string
                source_json = json.dumps(tweet)
                
                # Insert tweet into tweets table
                c.execute("""
                    INSERT INTO tweets 
                    (anchor_rest_id, rest_id, sort_index, user_id, screen_name, created_at, full_text, 
                    bookmarked, liked, first_in_thread, last_in_thread, source_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (anchor_rest_id, rest_id) DO UPDATE SET
                    sort_index = EXCLUDED.sort_index,
                    user_id = EXCLUDED.user_id,
                    screen_name = EXCLUDED.screen_name,
                    created_at = EXCLUDED.created_at,
                    full_text = EXCLUDED.full_text,
                    bookmarked = EXCLUDED.bookmarked,
                    liked = EXCLUDED.liked,
                    first_in_thread = EXCLUDED.first_in_thread,
                    last_in_thread = EXCLUDED.last_in_thread,
                    source_json = EXCLUDED.source_json
                """, (
                    anchor_rest_id, rest_id, sort_index, user_id, screen_name, created_at, full_text,
                    bookmarked, liked, first_in_thread, last_in_thread, source_json
                ))
                connection.commit()
        
        # Mark the tweet as expanded in tweet_index
        c.execute("UPDATE tweet_index SET expanded = True WHERE rest_id = %s", (anchor_rest_id,))
        connection.commit()


def fetch_data(url, headers):
    # print(f"fetching data from {url}")
    response = requests.get(url, headers=headers)
    return response.json()


def parse_entries_for_index(entries) -> tuple[list[tuple[str, str, str, str, str, str, str, str]], str | None]:
    parsed_data = []
    next_cursor = None
    for entry in entries:
        if 'content' in entry and 'itemContent' in entry['content']:
            result = entry['content']['itemContent']['tweet_results']['result']
            if result['__typename'] == 'TweetWithVisibilityResults':
                result = result['tweet']
            rest_id = result['rest_id']
            sort_index = entry['sortIndex']
            user_id = result['core']['user_results']['result']['rest_id']
            screen_name = result['core']['user_results']['result']['legacy']['screen_name']
            created_at = result['legacy']['created_at']
            bookmarked = result['legacy']['bookmarked']
            liked = result['legacy']['favorited']
            entry_json = json.dumps(entry)
            parsed_data.append((rest_id, sort_index, user_id, screen_name, created_at, bookmarked, liked, entry_json))
        elif 'content' in entry and 'cursorType' in entry['content'] and entry['content']['cursorType'] == "Bottom":
            next_cursor = entry['content']['value']
    return parsed_data, next_cursor


def construct_next_url(initial_url, cursor):
    return str.replace(initial_url, 'includePromotedContent',
                       'cursor%22%3A%22' + cursor + '%3D%3D%22%2C%22includePromotedContent')


def random_transaction_id() -> str:
    random_data = ''.join(random.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789', k=70))
    return base64.urlsafe_b64encode(random_data.encode()).decode()


def extract_likes(json_data):
    return json_data['data']['user']['result']['timeline_v2']['timeline']['instructions'][0]['entries']


def extract_bookmarks(json_data):
    return json_data['data']['bookmark_timeline_v2']['timeline']['instructions'][0]['entries']


def fetch_until_done(fetch_command, extractor):
    # Strip off the outer fetch and split into URL and headers
    url, headers = fetch_command[6:-2].split(',', 1)
    original_url = url.strip().strip('"')
    next_url = original_url
    headers = json.loads(headers.strip())

    # Update the transaction id
    headers['headers']['x-client-transaction-id'] = random_transaction_id()

    while True:
        # Fetch the data
        json_data = fetch_data(next_url, headers['headers'])

        entries = extractor(json_data)

        print(f"got {len(entries)} entries")

        # Parse the data
        data: list[tuple[str, str, str, str, str, str, str, str]]
        data, next_cursor = parse_entries_for_index(entries)

        # Save to database
        up_to_date = save_tweets_to_database_index(connection, data)

        if up_to_date:
            print("up to date")
            break

        if not next_cursor:
            print("end of list reached")
            break

        # Construct the next URL
        print(f"next cursor is {next_cursor}")
        next_cursor_encoded = quote(next_cursor.replace('=', ''))
        # print(f"next cursor urlencoded is {next_cursor_encoded}")
        next_url = construct_next_url(original_url, next_cursor_encoded)
        # print(f"next url is {next_url}")

        # Update the transaction id
        headers['headers']['x-client-transaction-id'] = random_transaction_id()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: uv run getbookmarks.py 'fetch(...)' [, 'fetch(...)', ...]")
        sys.exit(1)
    else:
        connection = initialize_database()

        tweet_fetch = None

        for arg in sys.argv[1:]:
            # Initialize the database
            if 'Bookmarks' in arg:
                print("fetching Bookmarks")
                fetch_until_done(arg, extract_bookmarks)
                print("Bookmarks done")
            elif 'Likes' in arg:
                print("fetching Likes")
                fetch_until_done(arg, extract_likes)
                print("Likes done")
            elif 'TweetDetail' in arg:
                tweet_fetch = arg
            else:
                print(f"unexpected fetch command: {arg}")
                
        # Iterate over tweets that are not yet expanded
        if tweet_fetch:
            print("expanding tweets")
            expand_tweets(tweet_fetch)
            print("tweets expanded")
        
        connection.close()
