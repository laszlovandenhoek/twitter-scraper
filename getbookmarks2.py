import json
from typing import Callable, Iterable, List, Optional, Set, Tuple
from pathlib import Path
from pydantic_core import ValidationError
from twitter_openapi_python import TimelineAddEntry, TimelineTimelineCursor, Tweet, TweetApiUtils, TwitterOpenapiPython, TweetApiUtilsData
from twitter_openapi_python.api.tweet_api import ResponseType

import configparser
import psycopg2

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

    with conn.cursor() as c:
        with open("init.sql", "r") as f:
            c.execute(f.read())
    
    conn.commit()
    
    print("database initialized")
    
    return conn

def get_client(endpoint: Optional[str] = None):
    if Path("cookie.json").exists():
        with open("cookie.json", "r") as f:
            cookies_dict = json.load(f)
            if isinstance(cookies_dict, list):
                cookies_dict = {k["name"]: k["value"] for k in cookies_dict}
    else:
        raise Exception("Cookie file not found")

    client = TwitterOpenapiPython()
    
    client.additional_api_headers = {
        "sec-ch-ua-platform": '"Windows"',
    }
    client.additional_browser_headers = {
        "sec-ch-ua-platform": '"Windows"',
    }
    # TODO: copy other ones from the browser

    # get client from cookies
    client = client.get_client_from_cookies(cookies=cookies_dict)
    if endpoint is not None:
        client.api.configuration.host = endpoint
    
    return client

def get_likes(api: TweetApiUtils, user_id: str, cursor: Optional[TimelineTimelineCursor] = None) -> ResponseType:
    print(f"getting likes for user {user_id} with cursor {cursor.value if cursor is not None else None}")
    return api.get_likes(user_id=user_id, count=20, cursor=cursor.value if cursor is not None else None)

def get_bookmarks(api: TweetApiUtils, cursor: Optional[TimelineTimelineCursor] = None) -> ResponseType:
    print(f"getting bookmarks with cursor {cursor.value if cursor is not None else None}")
    return api.get_bookmarks(count=20, cursor=cursor.value if cursor is not None else None)
    
def get_tweet_detail(api: TweetApiUtils, tweet_id: str, cursor: Optional[TimelineTimelineCursor] = None) -> ResponseType:
    print(f"getting tweet detail for tweet {tweet_id} with cursor {cursor.value if cursor is not None else None}")
    return api.get_tweet_detail(focal_tweet_id=tweet_id, cursor=cursor.value if cursor is not None else None)

def is_tweet_in_index(tweet: Tweet, conn: psycopg2.extensions.connection) -> bool:
    rest_id = tweet.rest_id
    
    with conn.cursor() as c:
        # this will skip over tweets that were included as part of a different list
        c.execute("SELECT COUNT(rest_id) FROM tweet_index WHERE rest_id = %s AND ((bookmarked != liked) AND source_json IS NOT NULL)", (rest_id,))
        row = c.fetchone()
        
    if row and row[0] > 0:
        print(f"found existing record for rest_id {rest_id}")
        return True
    else:
        return False

def save_index_tweet(timeline_add_entry: TimelineAddEntry, tweet_data: TweetApiUtilsData, conn: psycopg2.extensions.connection):
    print(f"saving index tweet {tweet_data.tweet.rest_id} with sort_index {timeline_add_entry.sort_index}")
    with conn.cursor() as c:
        c.execute("""
            INSERT INTO tweet_index (
                rest_id,
                conversation_id,
                sort_index,
                user_id,
                created_at,
                bookmarked,
                liked,
                source_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (rest_id) DO UPDATE SET bookmarked = EXCLUDED.bookmarked, liked = EXCLUDED.liked, source_json = EXCLUDED.source_json
        """, (
            tweet_data.tweet.rest_id,
            tweet_data.tweet.legacy.conversation_id_str,
            timeline_add_entry.sort_index,
            tweet_data.tweet.legacy.user_id_str,
            tweet_data.tweet.legacy.created_at,
            tweet_data.tweet.legacy.bookmarked,
            tweet_data.tweet.legacy.favorited,
            timeline_add_entry.to_json()))
    conn.commit()

def find_latest_pure_like(conn: psycopg2.extensions.connection) -> str | None:
    with conn.cursor() as c:
        c.execute("SELECT rest_id FROM tweet_index WHERE liked = true and bookmarked = false ORDER BY sort_index DESC LIMIT 1")
        row = c.fetchone()
        return row[0] if row else None

def find_latest_pure_bookmark(conn: psycopg2.extensions.connection) -> str | None:
    with conn.cursor() as c:
        c.execute("SELECT rest_id FROM tweet_index WHERE bookmarked = true and liked = false ORDER BY sort_index DESC LIMIT 1")
        row = c.fetchone()
        return row[0] if row else None

def dump_response(response: ResponseType, file_name: str):
    with open(file_name, "w") as f:
        json.dump(response.model_dump(mode="json"), f, indent=2)

def ellipsize(text: str) -> str:
    max_length = 20
    return text[:max_length] + '…' if len(text) > max_length else text

def find_timeline_start(fetch_fn: Callable[[Optional[TimelineTimelineCursor]], ResponseType], cursor: Optional[TimelineTimelineCursor] = None) -> TimelineTimelineCursor:
    print(f"Looking for timeline start at {cursor.value if cursor is not None else None}")
    
    try:
        response: ResponseType = fetch_fn(cursor)
    except ValidationError as e:
        print(f"Error fetching timeline start: {e}")
        return None
    
    if len(response.data.data) == 0:
        # Top found, return as starting point
        print(f"\tTop found, returning bottom cursor {response.data.cursor.bottom.value}")
        return response.data.cursor.bottom
    else:
        # Scroll further up
        print(f"\tTop not found, scrolling further up to {response.data.cursor.top.value}")
        return find_timeline_start(fetch_fn, response.data.cursor.top)

def paginate(fetch_fn: Callable[[Optional[TimelineTimelineCursor]], ResponseType], cursor: Optional[TimelineTimelineCursor] = None, stop_at_rest_id: Optional[str] = None) -> Iterable[Tuple[TimelineAddEntry, TweetApiUtilsData]]:
    
    effective_cursor = cursor or find_timeline_start(fetch_fn)

    response: ResponseType = fetch_fn(effective_cursor)

    # Get the raw entries that contain sort_index
    raw_entries: List[TimelineAddEntry] = response.data.raw.entry

    if len(response.data.data) == 0:
        print("End of timeline reached")
        return
    
    # You'll need to correlate these with your tweet data
    # The entries are in the same order as the processed tweets
    for i, tweet_data in enumerate(response.data.data):
        timeline_item = raw_entries[i]
        if tweet_data.tweet.rest_id == stop_at_rest_id:
            print(f"Stopping at tweet {tweet_data.tweet.rest_id} with sort_index {stop_at_rest_id}")
            return
        else:
            print(f"{tweet_data.tweet.rest_id} != {stop_at_rest_id}")
        yield timeline_item, tweet_data

    yield from paginate(fetch_fn, response.data.cursor.bottom, stop_at_rest_id)

def expand_tweet(tweet_id: str, conn: psycopg2.extensions.connection, alt_paths: Set[str] = set()) -> bool:
    tweet_detail_response: ResponseType = get_tweet_detail(tweet_api, tweet_id)

    top = tweet_detail_response.data.cursor.top
    if top is not None:
        print(f"TODO: implement top cursor")
        conn.rollback()
        return False

    bottom = tweet_detail_response.data.cursor.bottom
    if bottom is not None:
        print(f"TODO: implement bottom cursor")
        conn.rollback()
        return False

    thread_author_user_id = tweet_detail_response.data.data[0].tweet.legacy.user_id_str

    # Get the raw entries that contain sort_index
    raw_entries: List[TimelineAddEntry] = tweet_detail_response.data.raw.entry

    # You'll need to correlate these with your tweet data
    # The entries are in the same order as the processed tweets
    for i, tweet_data in enumerate(tweet_detail_response.data.data):
        sort_index = raw_entries[i].sort_index

        rest_id = tweet_data.tweet.rest_id
        replies = tweet_data.replies
        quoted = tweet_data.quoted

        if quoted is not None:
            print(f"TODO: implement quoted")
            conn.rollback()
            return False

        retweeted = tweet_data.retweeted
        if retweeted is not None:
            print(f"TODO: implement retweeted")
            conn.rollback()
            return False

        conversation_id = tweet_data.tweet.legacy.conversation_id_str

        # rest_id = tweet['rest_id']
        # sort_index = entry['sortIndex']
        # user_id = tweet['core']['user_results']['result']['rest_id']
        # screen_name = tweet['core']['user_results']['result']['legacy']['screen_name']
        # created_at = tweet['legacy']['created_at']
        # full_text = tweet['legacy']['full_text']
        # bookmarked = tweet['legacy'].get('bookmarked', False)
        # liked = tweet['legacy'].get('favorited', False)

        print(f"rest_id: {rest_id}")
        print(f"\tsort_index: {sort_index}")
        print(f"\ttext: {ellipsize(tweet_data.tweet.legacy.full_text)}")
        print(f"\tconversation_id: {conversation_id}")

        for reply in replies:
            if reply.user.rest_id == thread_author_user_id:
                if reply.tweet.rest_id in alt_paths:
                    print("thread fork cycle, bailing out")
                    return False
                else:
                    print(f"thread fork, expanding {reply.tweet.rest_id} instead")
                    earlier_items = [item.tweet.rest_id for item in tweet_detail_response.data.data]
                    conn.rollback()
                    return expand_tweet(reply.tweet.rest_id, conn, set(earlier_items).union(alt_paths))
            print(f"\treply: {reply.tweet.rest_id} in reply to {reply.tweet.legacy.in_reply_to_status_id_str}")
            print(f"\t\ttext: {ellipsize(reply.tweet.legacy.full_text)}")

if __name__ == '__main__':
    conn = initialize_database()

    client = get_client()

    tweet_api = client.get_tweet_api()

    def get_likes_with_cursor(cursor: Optional[TimelineTimelineCursor]) -> ResponseType:
        return get_likes(tweet_api, "117787606", cursor)

    def get_bookmarks_with_cursor(cursor: Optional[TimelineTimelineCursor]) -> ResponseType:
        return get_bookmarks(tweet_api, cursor)
    
    latest_like = find_latest_pure_like(conn)
    latest_bookmark = find_latest_pure_bookmark(conn)

    print(f"latest like: {latest_like}")
    likes_with_sort_index: Iterable[Tuple[TimelineAddEntry, TweetApiUtilsData]] = paginate(get_likes_with_cursor, None, latest_like)
    
    print(f"latest bookmark: {latest_bookmark}")
    bookmarks_with_sort_index: Iterable[Tuple[TimelineAddEntry, TweetApiUtilsData]] = paginate(get_bookmarks_with_cursor, None, latest_bookmark)

    for (timeline_add_entry, tweet_data) in likes_with_sort_index:
        save_index_tweet(timeline_add_entry, tweet_data, conn)
    
    for (timeline_add_entry, tweet_data) in bookmarks_with_sort_index:
        save_index_tweet(timeline_add_entry, tweet_data, conn)

    # expand_tweet("775730673420111872", conn)
    # expand_tweet("1628567045800591361", conn)
    # expand_tweet("1860347907615924669", conn)
    # expand_tweet("1452963540407668745", conn)
