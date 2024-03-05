import base64
import json
import random
import sys
from urllib.parse import quote

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
        CREATE TABLE IF NOT EXISTS tweets (
            rest_id VARCHAR(20) PRIMARY KEY,
            sort_index VARCHAR(20) NOT NULL,
            screen_name TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            fetched_at TIMESTAMP NOT NULL DEFAULT NOW(),
            full_text TEXT NOT NULL,
            bookmarked BOOLEAN NOT NULL DEFAULT False,
            liked BOOLEAN NOT NULL DEFAULT False,
            important BOOLEAN NOT NULL DEFAULT False,
            archived BOOLEAN NOT NULL DEFAULT False
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


def save_tweets_to_database(conn, items: list[tuple[str, str, str, str, str, str, str]]):
    c = conn.cursor()
    # Check if we have data
    if not items:
        return True

    # Check if we have been here before
    for item in items:
        rest_id = int(item[0])

        # this will skip over tweets that were included as part of a different list
        c.execute(f"SELECT COUNT(rest_id) FROM tweets WHERE rest_id = '%s' AND bookmarked != liked", (rest_id,))
        row = c.fetchone()

        if row[0] > 0:
            print(f"found existing bookmarked record for rest_id {rest_id}")
            return True

        # New data, insert it
        c.execute(
            "INSERT INTO tweets (rest_id, sort_index, screen_name, created_at, bookmarked, liked, full_text) VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (rest_id) DO UPDATE SET bookmarked = EXCLUDED.bookmarked, liked = EXCLUDED.liked",
            item)
        conn.commit()
        print(f"added rest_id {rest_id}")

    return False


def fetch_data(url, headers):
    # print(f"fetching data from {url}")
    response = requests.get(url, headers=headers)
    return response.json()


def parse_entries(entries) -> tuple[list[tuple[str, str, str, str, str, str, str]], str | None]:
    parsed_data = []
    next_cursor = None
    for entry in entries:
        if 'content' in entry and 'itemContent' in entry['content']:
            result = entry['content']['itemContent']['tweet_results']['result']
            if result['__typename'] == 'TweetWithVisibilityResults':
                result = result['tweet']
            rest_id = result['rest_id']
            sort_index = entry['sortIndex']
            screen_name = result['core']['user_results']['result']['legacy']['screen_name']
            created_at = result['legacy']['created_at']
            full_text = (
                result['note_tweet']['note_tweet_results']['result']['text'] if 'note_tweet' in result
                else result['legacy']['full_text']
            )
            bookmarked = result['legacy']['bookmarked']
            liked = result['legacy']['favorited']
            parsed_data.append((rest_id, sort_index, screen_name, created_at, bookmarked, liked, full_text))
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
        data: list[tuple[str, str, str, str, str, str, str]]
        data, next_cursor = parse_entries(entries)

        # Save to database
        up_to_date = save_tweets_to_database(connection, data)

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
        print("Usage: python3 getbookmarks.py 'fetch(...)' [, 'fetch(...)', ...]")
        sys.exit(1)
    else:
        connection = initialize_database()

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
            else:
                print(f"unexpected fetch command: {arg}")
        connection.close()
