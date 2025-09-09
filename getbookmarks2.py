import json
from typing import Callable, Iterable, List, Optional, Set, Tuple
from pathlib import Path
from pydantic_core import ValidationError
from twitter_openapi_python import (
    CursorType,
    TimelineAddEntry,
    TimelineTimelineCursor,
    Tweet,
    TweetApiUtils,
    TwitterOpenapiPython,
    TweetApiUtilsData,
    TypeName,
)
from twitter_openapi_python.api.tweet_api import ResponseType

import configparser
import psycopg2
import psycopg2.extensions


def initialize_database():
    # Initialize the parser and read the configuration file
    config = configparser.ConfigParser()
    config.read("db_config.ini")

    # Connect to the PostgreSQL server
    conn = psycopg2.connect(
        host=config["postgresql"]["host"],
        dbname=config["postgresql"]["dbname"],
        user=config["postgresql"]["user"],
        password=config["postgresql"]["password"],
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


def get_likes(
    api: TweetApiUtils, user_id: str, cursor: Optional[TimelineTimelineCursor] = None
) -> ResponseType:
    print(
        f"\t\t\tgetting likes for user {user_id} with cursor {cursor.value if cursor is not None else None}"
    )
    return api.get_likes(
        user_id=user_id, count=20, cursor=cursor.value if cursor is not None else None
    )


def get_bookmarks(
    api: TweetApiUtils, cursor: Optional[TimelineTimelineCursor] = None
) -> ResponseType:
    print(
        f"\t\t\tgetting bookmarks with cursor {cursor.value if cursor is not None else None}"
    )
    return api.get_bookmarks(
        count=20, cursor=cursor.value if cursor is not None else None
    )


def get_tweet_detail(
    api: TweetApiUtils, tweet_id: str, cursor: Optional[TimelineTimelineCursor] = None
) -> ResponseType:
    print(
        f"getting tweet detail for tweet {tweet_id} with cursor {cursor.value if cursor is not None else None}"
    )
    return api.get_tweet_detail(
        focal_tweet_id=tweet_id, cursor=cursor.value if cursor is not None else None
    )


def should_stop_after_tweet(conn: psycopg2.extensions.connection, tweet: Tweet, current_fetch_id: Optional[int] = None) -> bool:
    rest_id = tweet.rest_id

    if (tweet.legacy.bookmarked == tweet.legacy.favorited):
        print(f"tweet {rest_id} has bookmarked and liked set to the same value, going to pretend it's not in the index to prevent edge cases")
        return False

    with conn.cursor() as c:
        # this will skip over tweets that were included as part of a different list
        if current_fetch_id is not None:
            c.execute(
                "SELECT fetched_at, fetch_id FROM tweet_index WHERE rest_id = %s AND ((bookmarked != liked) AND fetch_id != %s)",
                    (rest_id, current_fetch_id),
                )
        else:
            c.execute(
                "SELECT fetched_at, fetch_id FROM tweet_index WHERE rest_id = %s AND ((bookmarked != liked))",
                (rest_id,),
            )
        
        row = c.fetchone()

        if row is not None:
            print(f"Tweet {rest_id} already exists in the index, fetched at {row[0]} from fetch {row[1]}")
            return True
        else:
            return False


def save_index_tweet(
    timeline_add_entry: TimelineAddEntry,
    tweet_data: TweetApiUtilsData,
    conn: psycopg2.extensions.connection,
    fetch_id: int,
):
    """
    Saves a tweet to the index with fetch tracking.

    Args:
        timeline_add_entry: The timeline entry containing sort_index
        tweet_data: The tweet data to save
        conn: Database connection
        fetch_id: Fetch ID this tweet belongs to
    """
    print(
        f"saving index tweet {tweet_data.tweet.rest_id} with sort_index {timeline_add_entry.sort_index}"
    )
    with conn.cursor() as c:
        c.execute(
            """
            INSERT INTO tweet_index (
                rest_id,
                conversation_id,
                sort_index,
                user_id,
                created_at,
                bookmarked,
                liked,
                source_json,
                fetch_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (rest_id) DO UPDATE SET 
                bookmarked = EXCLUDED.bookmarked, 
                liked = EXCLUDED.liked, 
                source_json = EXCLUDED.source_json,
                fetch_id = EXCLUDED.fetch_id
        """,
            (
                tweet_data.tweet.rest_id,
                tweet_data.tweet.legacy.conversation_id_str,
                timeline_add_entry.sort_index,
                tweet_data.tweet.legacy.user_id_str,
                tweet_data.tweet.legacy.created_at,
                tweet_data.tweet.legacy.bookmarked,
                tweet_data.tweet.legacy.favorited,
                timeline_add_entry.to_json(),
                fetch_id,
            ),
        )


def dump_response(response: ResponseType, file_name: str):
    with open(file_name, "w") as f:
        json.dump(response.model_dump(mode="json"), f, indent=2)


def ellipsize(text: str) -> str:
    max_length = 20
    return text[:max_length] + "…" if len(text) > max_length else text


def find_timeline_start(
    fetch_fn: Callable[[Optional[TimelineTimelineCursor]], ResponseType],
    cursor: Optional[TimelineTimelineCursor] = None,
    top_tweet: Optional[Tweet] = None,
) -> Tuple[TimelineTimelineCursor, Optional[Tweet]]:
    """
    Finds the start of the timeline and returns the cursor and the first tweet (if it exists).
    """
    print(
        f"\t\tLooking for timeline start at {cursor.value if cursor is not None else None}"
    )

    try:
        response: ResponseType = fetch_fn(cursor)
    except ValidationError as e:
        print(f"Error fetching timeline start: {e}")
        raise e

    if len(response.data.data) == 0:
        # Top found, return as starting point
        print(
            f"\t\t\tTop found, returning bottom cursor {response.data.cursor.bottom.value}"
        )
        return response.data.cursor.bottom, top_tweet
    else:
        # Scroll further up
        print(
            f"\t\t\tTop not found, scrolling further up to {response.data.cursor.top.value}"
        )
        return find_timeline_start(
            fetch_fn,
            cursor=response.data.cursor.top,
            top_tweet=response.data.data[0].tweet,
        )


def paginate(
    conn: psycopg2.extensions.connection,
    fetch_fn: Callable[[Optional[TimelineTimelineCursor]], ResponseType],
    cursor: TimelineTimelineCursor,
    current_fetch_id: int,
) -> Iterable[Tuple[str, TimelineAddEntry, TweetApiUtilsData]]:
    response: ResponseType = fetch_fn(cursor)

    # Get the raw entries that contain sort_index
    raw_entries: List[TimelineAddEntry] = response.data.raw.entry

    if len(response.data.data) == 0:
        print("End of timeline reached")
        return

    # You'll need to correlate these with your tweet data
    # The entries are in the same order as the processed tweets
    for i, tweet_data in enumerate(response.data.data):
        timeline_item = raw_entries[i]

        # Check if this tweet already exists in the database
        if should_stop_after_tweet(conn, tweet_data.tweet, current_fetch_id):
            print(f"Stopping fetch after existing tweet {tweet_data.tweet.rest_id}")
            return

        yield cursor.value, timeline_item, tweet_data

    yield from paginate(conn, fetch_fn, response.data.cursor.bottom, current_fetch_id)


def expand_tweet(
    tweet_id: str, conn: psycopg2.extensions.connection, alt_paths: Set[str] = set()
) -> bool:
    # Start a transaction
    conn.autocommit = False

    tweet_detail_response: ResponseType = get_tweet_detail(tweet_api, tweet_id)

    top = tweet_detail_response.data.cursor.top
    if top is not None:
        print("TODO: implement top cursor")
        conn.rollback()
        return False

    bottom = tweet_detail_response.data.cursor.bottom
    if bottom is not None:
        print("TODO: implement bottom cursor")
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
            print("TODO: implement quoted")
            conn.rollback()
            return False

        retweeted = tweet_data.retweeted
        if retweeted is not None:
            print("TODO: implement retweeted")
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
                    earlier_items = [
                        item.tweet.rest_id for item in tweet_detail_response.data.data
                    ]
                    conn.rollback()
                    return expand_tweet(
                        reply.tweet.rest_id, conn, set(earlier_items).union(alt_paths)
                    )
            print(
                f"\treply: {reply.tweet.rest_id} in reply to {reply.tweet.legacy.in_reply_to_status_id_str}"
            )
            print(f"\t\ttext: {ellipsize(reply.tweet.legacy.full_text)}")

    # after all replies are processed, mark the tweet as expanded.
    mark_tweet_as_expanded(rest_id, conn)
    conn.commit()


def get_unexpanded_tweet_ids(conn: psycopg2.extensions.connection) -> Iterable[str]:
    """
    Lazily fetches the rest_id's of all tweets that are not expanded.

    Args:
        conn: Database connection

    Yields:
        str: rest_id of each unexpanded tweet
    """
    with conn.cursor() as c:
        c.execute(
            "SELECT rest_id FROM tweet_index WHERE expanded = false ORDER BY sort_index DESC"
        )

        while True:
            rows = c.fetchmany(100)  # Fetch in batches of 100
            if not rows:
                break

            for row in rows:
                yield row[0]


def mark_tweet_as_expanded(rest_id: str, conn: psycopg2.extensions.connection):
    """
    Marks a tweet as expanded in the database.

    Args:
        rest_id: The rest_id of the tweet to mark as expanded
        conn: Database connection
    """
    with conn.cursor() as c:
        c.execute(
            "UPDATE tweet_index SET expanded = true WHERE rest_id = %s", (rest_id,)
        )


def create_fetch(
    conn: psycopg2.extensions.connection,
    is_likes: bool,
    is_bookmarks: bool,
    start_cursor: str,
) -> int:
    """
    Starts a new fetch operation and returns the fetch ID.

    Args:
        conn: Database connection
        is_likes: Whether this fetch is for likes
        is_bookmarks: Whether this fetch is for bookmarks
        start_cursor: The starting cursor for the fetch

    Returns:
        int: The fetch ID
    """
    with conn.cursor() as c:
        c.execute(
            """
            INSERT INTO fetches (is_likes, is_bookmarks, start_cursor)
            VALUES (%s, %s, %s)
            RETURNING id
        """,
            (is_likes, is_bookmarks, start_cursor),
        )
        fetch_id = c.fetchone()[0]
    
    return fetch_id


def update_fetch_cursor(
    conn: psycopg2.extensions.connection, fetch_id: int, cursor: str
):
    """
    Updates the last cursor for a fetch operation.

    Args:
        conn: Database connection
        fetch_id: The fetch ID to update
        cursor: The new cursor value
    """
    print(f"Updating fetch {fetch_id} with cursor {cursor}")
    with conn.cursor() as c:
        c.execute(
            "UPDATE fetches SET last_cursor = %s WHERE id = %s", (cursor, fetch_id)
        )


def finish_fetch(conn: psycopg2.extensions.connection, fetch_id: int):
    """
    Marks a fetch operation as finished.

    Args:
        conn: Database connection
        fetch_id: The fetch ID to finish
    """
    with conn.cursor() as c:
        c.execute("UPDATE fetches SET finished_at = NOW() WHERE id = %s", (fetch_id,))
    print(f"Finished fetch {fetch_id}")


def get_fetches_worklist(
    conn: psycopg2.extensions.connection,
) -> List[Tuple[int, bool, bool, str, Optional[str]]]:
    """
    Gets all incomplete fetch operations in chronological order (oldest first).

    Args:
        conn: Database connection

    Returns:
        List of tuples: (fetch_id, is_likes, is_bookmarks, start_cursor, last_cursor)
    """
    with conn.cursor() as c:
        c.execute("""
            SELECT id, is_likes, is_bookmarks, start_cursor, last_cursor
            FROM fetches
            WHERE finished_at IS NULL
            ORDER BY started_at ASC
        """)
        return c.fetchall()


def process_fetch(
    conn: psycopg2.extensions.connection,
    fetch_id: int,
    last_cursor: str,
    fetch_fn: Callable[[Optional[TimelineTimelineCursor]], ResponseType],
) -> None:
    """
    Processes a single fetch operation (either new or resumed).

    Args:
        conn: Database connection
        fetch_id: The fetch ID to process
        last_cursor: The cursor to start from (None for new fetches)
        fetch_fn: Function to get tweets

    Returns:
        bool: True if fetch completed successfully, False otherwise
    """
    try:
        # Create cursor object from the last cursor (None for new fetches)
        cursor = (
            TimelineTimelineCursor(
                typename=TypeName.TIMELINETIMELINECURSOR,
                cursor_type=CursorType.BOTTOM,
                value=last_cursor,
            )
            if last_cursor
            else None
        )

        # Fetch tweets with progress tracking - paginate will stop when it finds existing tweets from a previous fetch
        for tweet_cursor, timeline_add_entry, tweet_data in paginate(
            conn, fetch_fn, cursor, fetch_id
        ):

            print(f"Processing tweet {tweet_data.tweet.rest_id} from fetch {fetch_id} at cursor {tweet_cursor}")

            # Update the fetch cursor as we progress
            update_fetch_cursor(conn, fetch_id, tweet_cursor)

            # Save the tweet with the fetch ID
            save_index_tweet(timeline_add_entry, tweet_data, conn, fetch_id)

        # Mark fetch as finished
        finish_fetch(conn, fetch_id)

        print(f"Successfully completed fetch {fetch_id}")

    except Exception as e:
        print(f"Error during fetch {fetch_id}: {e}")
        raise e


def create_new_fetches(
    conn: psycopg2.extensions.connection,
    get_likes: Callable[[Optional[TimelineTimelineCursor]], ResponseType],
    get_bookmarks: Callable[[Optional[TimelineTimelineCursor]], ResponseType],
) -> None:
    """
    Creates new fetches and processes them, as well as incomplete fetches, in chronological order.
    """
    for fetch_type in ["likes", "bookmarks"]:
        # Find the proper starting cursor from the timeline
        print(f"\tFinding timeline start for {fetch_type}...")
        start_cursor, top_tweet = find_timeline_start(
            get_likes if fetch_type == "likes" else get_bookmarks
        )
        if top_tweet:
            print(f"\t\tFound timeline start cursor: {start_cursor.value}, first tweet: {top_tweet.rest_id}")
        else:
            print(f"\t\tNo first tweet found, skipping {fetch_type}")
            continue

        if should_stop_after_tweet(conn, top_tweet):
            print(f"\t\t\tFirst tweet is already in the index, skipping {fetch_type}")
            continue

        with conn.cursor() as c:
            c.execute(
                "SELECT id, finished_at FROM fetches WHERE finished_at is not null and is_likes = %s AND is_bookmarks = %s AND start_cursor = %s and last_cursor = %s",
                (fetch_type == "likes", fetch_type == "bookmarks", start_cursor.value, start_cursor.value),
            )
            previous_fetch = c.fetchone()
            if previous_fetch:
                print(f"\t\tPreviously completed fetch {previous_fetch[0]} already started at cursor {start_cursor.value} at {previous_fetch[1]}, skipping {fetch_type}")
                continue

        # Start a new fetch with the proper cursor
        fetch_id = create_fetch(
            conn, fetch_type == "likes", fetch_type == "bookmarks", start_cursor.value
        )
        print(f"\t\tCreated new {fetch_type} fetch {fetch_id}")


def run_fetches(
    conn: psycopg2.extensions.connection,
    get_likes: Callable[[Optional[TimelineTimelineCursor]], ResponseType],
    get_bookmarks: Callable[[Optional[TimelineTimelineCursor]], ResponseType],
) -> None:
    # Get all incomplete fetches of this type in chronological order (oldest first)
    unfinished_fetches = get_fetches_worklist(conn)

    if not unfinished_fetches:
        print("\tNo incomplete fetches found")
        return

    print(f"\tProcessing {len(unfinished_fetches)} fetches...")

    # Process each incomplete fetch of this type
    for (
        fetch_id,
        fetch_is_likes,
        fetch_is_bookmarks,
        start_cursor,
        last_cursor,
    ) in unfinished_fetches:
        effective_cursor = last_cursor if last_cursor else start_cursor
        fetch_type = (
            "Likes"
            if fetch_is_likes
            else "Bookmarks"
            if fetch_is_bookmarks
            else "Unknown"
        )

        print(
            f"\t\t{'Resuming' if last_cursor else 'Starting'} {fetch_type} fetch {fetch_id} from cursor {effective_cursor}"
        )

        if fetch_is_likes:
            fetch_fn = get_likes
        elif fetch_is_bookmarks:
            fetch_fn = get_bookmarks
        else:
            raise ValueError(f"Invalid fetch type for fetch {fetch_id}")

        process_fetch(conn, fetch_id, effective_cursor, fetch_fn)

    print("\tSuccessfully processed fetches")


def fetch_all(
    conn: psycopg2.extensions.connection,
    tweet_api: TweetApiUtils,
    user_id: Optional[str] = None,
) -> bool:
    """
    Creates new fetches and processes them, as well as incomplete fetches, in chronological order.

    Args:
        conn: Database connection
        tweet_api: Twitter API utils
        user_id: User ID for likes fetching (required if is_likes=True)

    Returns:
        bool: True if all fetches completed successfully, False otherwise
    """

    def get_likes_from_cursor(cursor: Optional[TimelineTimelineCursor]) -> ResponseType:
        return get_likes(tweet_api, user_id, cursor)

    def get_bookmarks_from_cursor(
        cursor: Optional[TimelineTimelineCursor],
    ) -> ResponseType:
        return get_bookmarks(tweet_api, cursor)

    # clean up any incomplete fetches
    print("Cleaning up any incomplete fetches...")
    run_fetches(conn, get_likes_from_cursor, get_bookmarks_from_cursor)

    # create new fetches
    print("Creating new fetches...")
    create_new_fetches(conn, get_likes_from_cursor, get_bookmarks_from_cursor)

    # run the new fetches
    print("Running new fetches...")
    run_fetches(conn, get_likes_from_cursor, get_bookmarks_from_cursor)


if __name__ == "__main__":
    conn = initialize_database()
    client = get_client()
    tweet_api = client.get_tweet_api()

    conn.autocommit = True

    # Process all fetches for likes and bookmarks
    print("Fetching...")
    fetch_all(conn, tweet_api, "117787606")

    # Example usage of the lazy fetch method
    unexpanded_tweets = get_unexpanded_tweet_ids(conn)
    for rest_id in unexpanded_tweets:
        # print(f"Processing unexpanded tweet: {rest_id}")
        # Here you would call expand_tweet(rest_id, conn) or similar
        # mark_tweet_as_expanded(rest_id, conn)
        pass

    # expand_tweet("775730673420111872", conn)
    # expand_tweet("1628567045800591361", conn)
    # expand_tweet("1860347907615924669", conn)
    # expand_tweet("1452963540407668745", conn)
