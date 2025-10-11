from datetime import datetime
import json
from itertools import chain
import time
from time import sleep
from typing import Callable, Iterable, List, Optional, Set, Tuple
from pathlib import Path
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


def determine_next_sleep(response: ResponseType):
    global next_reset
    next_reset = datetime.fromtimestamp(response.header.rate_limit_reset)

    remaining = response.header.rate_limit_remaining

    if remaining <= 10:
        time_left: float = response.header.rate_limit_reset - time.time()
        time_per_request = time_left / remaining
        wait_time = time_per_request + 10
    else:
        wait_time = 1
    return wait_time


next_sleep: float = 0
next_reset: Optional[datetime] = None


def get_likes(
    api: TweetApiUtils, user_id: str, cursor: Optional[TimelineTimelineCursor] = None
) -> ResponseType:
    global next_sleep
    print(
        f"\t\t\tgetting likes for user {user_id} with cursor {cursor.value if cursor is not None else None} (in {round(next_sleep, 1)}s, next reset at {next_reset})"
    )
    sleep(next_sleep)
    response = api.get_likes(
        user_id=user_id, count=20, cursor=cursor.value if cursor is not None else None
    )

    next_sleep = determine_next_sleep(response)
    return response


def get_bookmarks(
    api: TweetApiUtils, cursor: Optional[TimelineTimelineCursor] = None
) -> ResponseType:
    global next_sleep
    print(
        f"\t\t\tgetting bookmarks with cursor {cursor.value if cursor is not None else None} (in {round(next_sleep, 1)}s, next reset at {next_reset})"
    )
    sleep(next_sleep)
    response = api.get_bookmarks(
        count=20, cursor=cursor.value if cursor is not None else None
    )
    next_sleep = determine_next_sleep(response)
    return response


def get_tweet_detail(
    api: TweetApiUtils, tweet_id: str, cursor: Optional[TimelineTimelineCursor] = None
) -> ResponseType:
    global next_sleep
    print(
        f"getting details for tweet {tweet_id} {cursor.cursor_type if cursor is not None else 'vanilla'} (in {round(next_sleep, 1)}s, next reset at {next_reset})"
    )
    sleep(next_sleep)

    response = api.get_tweet_detail(
        focal_tweet_id=tweet_id, cursor=cursor.value if cursor is not None else None
    )

    next_sleep = determine_next_sleep(response)
    return response


def should_stop_after_tweet(
    conn: psycopg2.extensions.connection,
    tweet: Tweet,
    current_fetch_id: Optional[int] = None,
) -> bool:
    rest_id = tweet.rest_id

    # this will not stop at tweets that were fetched earlier, but as part of a different list
    if tweet.legacy.bookmarked == tweet.legacy.favorited:
        print(
            f"tweet {rest_id} has bookmarked and liked set to the same value, going to pretend it's not in the index to prevent edge cases"
        )
        return False

    with conn.cursor() as c:
        if current_fetch_id is not None:
            c.execute(
                "SELECT fetched_at, fetch_id FROM tweet_index WHERE rest_id = %s AND bookmarked = %s and liked = %s AND fetch_id != %s",
                (
                    rest_id,
                    tweet.legacy.bookmarked,
                    tweet.legacy.favorited,
                    current_fetch_id,
                ),
            )
        else:
            c.execute(
                "SELECT fetched_at, fetch_id FROM tweet_index WHERE rest_id = %s AND bookmarked = %s and liked = %s",
                (rest_id, tweet.legacy.bookmarked, tweet.legacy.favorited),
            )

        row = c.fetchone()

        if row is not None:
            print(
                f"Tweet {rest_id} already exists in the index, fetched at {row[0]} from fetch {row[1]}"
            )
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


def paginate(
    fetch_fn: Callable[[TimelineTimelineCursor | None], ResponseType],
    stop_fn: Callable[
        [Tweet], bool
    ] = lambda x: False,  # by default, don't stop until we reach the end
    cursor: TimelineTimelineCursor | None = None,
    direction: CursorType = CursorType.BOTTOM,
) -> Iterable[Tuple[TimelineTimelineCursor, TimelineAddEntry, TweetApiUtilsData]]:
    response: ResponseType = fetch_fn(cursor)

    # Get the raw entries that contain sort_index
    raw_entries: List[TimelineAddEntry] = response.data.raw.entry

    if len(response.data.data) == 0:
        print(f"{direction.value} reached")
        return

    if direction == CursorType.TOP:
        # For upward pagination, we need to apply stop_fn in reverse order
        # First, scan current page's tweets in reverse order to find stop point
        tweets_to_yield = []
        should_continue_pagination = True

        # Process tweets in reverse order to find where to stop
        for i in range(len(response.data.data) - 1, -1, -1):
            tweet_data = response.data.data[i]
            timeline_item = raw_entries[i]

            if stop_fn(tweet_data.tweet):
                print(
                    f"Stopping fetch after tweet {tweet_data.tweet.rest_id} (found in upward pagination)"
                )
                should_continue_pagination = False
                break

            # Add to front of list to maintain original order
            tweets_to_yield.insert(0, (cursor, timeline_item, tweet_data))

        # Only continue pagination if no stop condition was found on current page
        if should_continue_pagination and response.data.cursor.top is not None:
            # Recursively get tweets from further up
            yield from paginate(fetch_fn, stop_fn, response.data.cursor.top, direction)

        # Yield tweets in original order
        for tweet_tuple in tweets_to_yield:
            yield tweet_tuple

    # Correlate TimelineAddEntry with TweetApiUtilsData (only for BOTTOM direction)
    if direction == CursorType.BOTTOM:
        for i, tweet_data in enumerate(response.data.data):
            timeline_item = raw_entries[i]

            # Check if we should stop at this tweet
            if stop_fn(tweet_data.tweet):
                print(f"Stopping fetch after tweet {tweet_data.tweet.rest_id}")
                return

            yield cursor, timeline_item, tweet_data

    if direction == CursorType.BOTTOM:
        yield from paginate(fetch_fn, stop_fn, response.data.cursor.bottom, direction)


def expand_tweet(
    conn: psycopg2.extensions.connection,
    tweet_api: TweetApiUtils,
    tweet_id: str,
    previously_expanded_tweet_ids: Set[str] = set(),
    anchor_rest_id: str | None = None,
    quote_depth: int = 0,
    breadcrumbs: List[Tuple[str, int, int]] = [],
) -> Set[str]:
    if anchor_rest_id is None:
        anchor_rest_id = tweet_id

    print(
        f"expanding tweet {tweet_id} with anchor_rest_id {anchor_rest_id} at quote depth {quote_depth}"
    )
    for idx, breadcrumb in enumerate(breadcrumbs):
        print(f"{idx}: {breadcrumb[0]} ({breadcrumb[1]}/{breadcrumb[2]})")

    def get_tweet_detail_from_cursor(
        cursor: TimelineTimelineCursor | None = None,
    ) -> ResponseType:
        return get_tweet_detail(tweet_api, tweet_id, cursor)

    initial_response: ResponseType = get_tweet_detail_from_cursor(None)

    # get all user_id_str and in_reply_to_user_id_str from the initial response
    significant_users: Set[str] = set()
    for tweet_data in initial_response.data.data:
        significant_users.add(tweet_data.user.rest_id)
        if tweet_data.tweet.legacy.in_reply_to_user_id_str is not None:
            significant_users.add(tweet_data.tweet.legacy.in_reply_to_user_id_str)

    def stop_on_insignificant_user(tweet: Tweet) -> bool:
        return tweet.legacy.user_id_str not in significant_users

    from_top_until_here: Iterable[
        Tuple[TimelineTimelineCursor, TimelineAddEntry, TweetApiUtilsData]
    ] = (
        iter([])
        if initial_response.data.cursor.top is None
        else paginate(
            get_tweet_detail_from_cursor,
            lambda tweet: tweet.rest_id in previously_expanded_tweet_ids,
            initial_response.data.cursor.top,
            CursorType.TOP,
        )
    )
    from_here_until_bottom: Iterable[
        Tuple[TimelineTimelineCursor, TimelineAddEntry, TweetApiUtilsData]
    ] = (
        iter([])
        if initial_response.data.cursor.bottom is None
        else paginate(
            get_tweet_detail_from_cursor,
            stop_on_insignificant_user,
            initial_response.data.cursor.bottom,
            CursorType.BOTTOM,
        )
    )

    initial_page_as_paginated: Iterable[
        Tuple[TimelineTimelineCursor, TimelineAddEntry, TweetApiUtilsData]
    ] = [
        ("", entry, data)
        for entry, data in zip(
            initial_response.data.raw.entry, initial_response.data.data
        )
    ]

    all_tweet_data: Iterable[
        Tuple[TimelineTimelineCursor, TimelineAddEntry, TweetApiUtilsData]
    ] = chain(from_top_until_here, initial_page_as_paginated, from_here_until_bottom)

    replies_to_expand: Set[str] = set()

    newly_expanded_tweet_ids: Set[str] = set()

    author_of_first_tweet = None

    for _, timeline_add_entry, tweet_data in all_tweet_data:
        if author_of_first_tweet is None:
            author_of_first_tweet = tweet_data.user.rest_id

        if tweet_data.promoted_metadata is not None:
            # skip promoted tweets
            print(f"skipping promoted tweet {tweet_data.tweet.rest_id}")
            continue

        in_reply_to_status_id_str = tweet_data.tweet.legacy.in_reply_to_status_id_str
        in_reply_to_user_id_str = tweet_data.tweet.legacy.in_reply_to_user_id_str

        sort_index = timeline_add_entry.sort_index

        rest_id = tweet_data.tweet.rest_id
        replies = tweet_data.replies

        retweeted = tweet_data.retweeted

        if retweeted is not None:
            print("TODO: implement retweeted")
            raise NotImplementedError("retweeted not implemented")

        conversation_id = tweet_data.tweet.legacy.conversation_id_str

        user_id = tweet_data.user.rest_id
        screen_name = tweet_data.user.legacy.screen_name
        created_at = tweet_data.tweet.legacy.created_at
        full_text = tweet_data.tweet.legacy.full_text
        bookmarked = tweet_data.tweet.legacy.bookmarked
        liked = tweet_data.tweet.legacy.favorited

        print(f"tweet {rest_id} by {screen_name}: {ellipsize(full_text)}")

        save_tweet(
            conn, sort_index, tweet_data, anchor_rest_id, timeline_add_entry.to_json()
        )

        newly_expanded_tweet_ids.add(rest_id)

        quoted = tweet_data.quoted
        if quoted is not None:
            if quote_depth > 3:
                print(
                    f"Reached maximum quote depth of 3, not recursing into quoted tweet {quoted.tweet.rest_id}"
                )
            else:
                # Recursively expand the quoted tweet to get its full conversation

                if quoted.tweet.rest_id in previously_expanded_tweet_ids.union(
                    newly_expanded_tweet_ids
                ):
                    print(
                        f"\t\tQuoted tweet {quoted.tweet.rest_id} already saved or expanded, skipping"
                    )

                else:
                    print(f"\t\tExpanding quoted tweet {quoted.tweet.rest_id}")
                    additionally_expanded_tweet_ids = expand_tweet(
                        conn,
                        tweet_api,
                        quoted.tweet.rest_id,
                        previously_expanded_tweet_ids.union(newly_expanded_tweet_ids),
                        anchor_rest_id,
                        quote_depth + 1,
                        breadcrumbs + [(quoted.tweet.rest_id, 1, 1)],
                    )
                    newly_expanded_tweet_ids.update(additionally_expanded_tweet_ids)

                # Register the quote relationship in the retweets table, regardless of whether the quoted tweet was already expanded
                save_retweet_relationship(
                    conn,
                    anchor_rest_id,
                    rest_id,  # The quote tweet
                    quoted.tweet.rest_id,  # The quoted tweet
                    is_quote=True,
                )

        for reply in replies:
            print(
                f"\treply: {reply.tweet.rest_id} in reply to {reply.tweet.legacy.in_reply_to_status_id_str}: {ellipsize(reply.tweet.legacy.full_text)}"
            )

            save_tweet(conn, sort_index, reply, anchor_rest_id)

            if reply.tweet.rest_id in previously_expanded_tweet_ids.union(
                newly_expanded_tweet_ids
            ):
                print(f"\t\tReply {reply.tweet.rest_id} already expanded, skipping")
                continue

            if reply.user.rest_id not in significant_users:
                print(
                    f"\t\tReply {reply.tweet.rest_id} is from an insignificant user, skipping"
                )
                continue
            else:
                print(
                    f"\t\t\tthread continues with {reply.tweet.rest_id}, expanding that too"
                )
                replies_to_expand.add(reply.tweet.rest_id)

                irt = reply.tweet.legacy.in_reply_to_status_id_str

                print(f"\t\t\tTweet is in reply to {irt}, so not expanding that")
                replies_to_expand.discard(irt)
                replies_to_expand.add(reply.tweet.rest_id)

    if len(replies_to_expand) > 0:
        print(f"\t\t{len(replies_to_expand)} replies to expand: {replies_to_expand}")

    for idx, reply_to_expand in enumerate(replies_to_expand):
        if reply_to_expand in previously_expanded_tweet_ids.union(
            newly_expanded_tweet_ids
        ):
            print(f"\t\t\tReply {reply_to_expand} already expanded, skipping")
            continue

        print(f"\t\t\tExpanding reply {reply_to_expand}")
        additionally_expanded_tweet_ids = expand_tweet(
            conn,
            tweet_api,
            reply_to_expand,
            previously_expanded_tweet_ids.union(newly_expanded_tweet_ids),
            anchor_rest_id,
            quote_depth,
            breadcrumbs + [(reply_to_expand, idx + 1, len(replies_to_expand))],
        )
        newly_expanded_tweet_ids.update(additionally_expanded_tweet_ids)

    print(
        f"Tweet {tweet_id} expanded successfully with {len(newly_expanded_tweet_ids)} newly expanded tweet(s) with anchor_rest_id {anchor_rest_id}"
    )
    return newly_expanded_tweet_ids


def save_tweet(
    conn: psycopg2.extensions.connection,
    sort_index: str,
    tweet_data: TweetApiUtilsData,
    anchor_rest_id: str,
    source_json: str = "{}",
):
    columns = [
        "anchor_rest_id",
        "conversation_id",
        "rest_id",
        "sort_index",
        "user_id",
        "screen_name",
        "created_at",
        "full_text",
        "bookmarked",
        "liked",
        "source_json",
    ]
    placeholders = ", ".join(["%s"] * len(columns))
    query = f"INSERT INTO tweets ({', '.join(columns)}) VALUES ({placeholders}) ON CONFLICT (anchor_rest_id, rest_id) DO UPDATE SET {', '.join([f'{column} = EXCLUDED.{column}' for column in columns])}"

    with conn.cursor() as c:
        c.execute(
            query,
            (
                anchor_rest_id,
                tweet_data.tweet.legacy.conversation_id_str,
                tweet_data.tweet.rest_id,
                sort_index,
                tweet_data.tweet.legacy.user_id_str,
                tweet_data.user.legacy.screen_name,
                tweet_data.tweet.legacy.created_at,
                tweet_data.tweet.legacy.full_text,
                tweet_data.tweet.legacy.bookmarked,
                tweet_data.tweet.legacy.favorited,
                source_json,
            ),
        )


def save_retweet_relationship(
    conn: psycopg2.extensions.connection,
    anchor_rest_id: str,
    rest_id: str,
    retweet_rest_id: str,
    is_quote: bool,
):
    """
    Saves a retweet/quote relationship in the retweets table.

    Args:
        conn: Database connection
        anchor_rest_id: The anchor rest_id of the tweet that contains the retweet/quote
        rest_id: The rest_id of the tweet that contains the retweet/quote
        retweet_rest_id: The rest_id of the retweeted/quoted tweet
        is_quote: True if this is a quote tweet, False if it's a retweet
    """
    with conn.cursor() as c:
        c.execute(
            """
            INSERT INTO retweets (
                anchor_rest_id,
                rest_id,
                retweet_rest_id,
                is_quote
            )
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (anchor_rest_id, rest_id) 
            DO NOTHING
        """,
            (anchor_rest_id, rest_id, retweet_rest_id, is_quote),
        )


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
) -> int:
    """
    Starts a new fetch operation and returns the fetch ID.

    Args:
        conn: Database connection
        is_likes: Whether this fetch is for likes
        is_bookmarks: Whether this fetch is for bookmarks

    Returns:
        int: The fetch ID
    """
    with conn.cursor() as c:
        c.execute(
            """
            INSERT INTO fetches (is_likes, is_bookmarks)
            VALUES (%s, %s)
            RETURNING id
        """,
            (is_likes, is_bookmarks),
        )
        fetch_id = c.fetchone()[0]

    return fetch_id


def update_fetch_cursor(
    conn: psycopg2.extensions.connection, fetch_id: int, cursor: TimelineTimelineCursor
):
    """
    Updates the last cursor for a fetch operation.

    Args:
        conn: Database connection
        fetch_id: The fetch ID to update
        cursor: The new cursor value
    """
    print(f"Updating fetch {fetch_id} with cursor {cursor.value}")
    with conn.cursor() as c:
        c.execute(
            "UPDATE fetches SET last_cursor = %s WHERE id = %s",
            (cursor.value, fetch_id),
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
) -> List[Tuple[int, bool, bool, Optional[str]]]:
    """
    Gets all incomplete fetch operations in chronological order (oldest first).

    Args:
        conn: Database connection

    Returns:
        List of tuples: (fetch_id, is_likes, is_bookmarks, last_cursor)
    """
    with conn.cursor() as c:
        c.execute("""
            SELECT id, is_likes, is_bookmarks, last_cursor
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

        def stop_fn(tweet: Tweet) -> bool:
            return should_stop_after_tweet(conn, tweet, fetch_id)

        # Fetch tweets with progress tracking - paginate will stop when it finds existing tweets from a previous fetch
        for tweet_cursor, timeline_add_entry, tweet_data in paginate(
            fetch_fn, stop_fn, cursor
        ):
            print(
                f"Processing tweet {tweet_data.tweet.rest_id} from fetch {fetch_id} at cursor {tweet_cursor.value if tweet_cursor is not None else None}"
            )

            if tweet_cursor is not None:
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
    for fetch_type in ["likes", "bookmarks"]:  # TODO: make fetch_type an enum
        print(f"\t getting first {fetch_type} page...")
        tweets: Iterable[
            Tuple[TimelineTimelineCursor, TimelineAddEntry, TweetApiUtilsData]
        ] = paginate(
            fetch_fn=get_likes if fetch_type == "likes" else get_bookmarks,
            stop_fn=lambda x: should_stop_after_tweet(conn, x),
        )

        _, _, first_tweet = next(tweets, (None, None, None))

        if not first_tweet:
            print(f"\t\tNo new tweets found, not creating new fetch for {fetch_type}")
            continue

        else:
            print(f"\t\tTimeline starts with new tweet: {first_tweet.tweet.rest_id}")

        # Start a new fetch with the proper cursor
        fetch_id = create_fetch(conn, fetch_type == "likes", fetch_type == "bookmarks")
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
        last_cursor,
    ) in unfinished_fetches:
        fetch_type = (
            "Likes"
            if fetch_is_likes
            else "Bookmarks"
            if fetch_is_bookmarks
            else "Unknown"
        )

        print(f"\t\tStarting {fetch_type} fetch {fetch_id} from cursor {last_cursor}")

        if fetch_is_likes:
            fetch_fn = get_likes
        elif fetch_is_bookmarks:
            fetch_fn = get_bookmarks
        else:
            raise ValueError(f"Invalid fetch type for fetch {fetch_id}")

        process_fetch(conn, fetch_id, last_cursor, fetch_fn)

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
    # client = get_client("http://localhost:8000")
    client = get_client()
    tweet_api = client.get_tweet_api()

    conn.autocommit = True

    # Process all fetches for likes and bookmarks
    print("Fetching...")
    fetch_all(conn, tweet_api, "117787606")

    conn.autocommit = False

    # expand_tweet(conn, tweet_api, "1962836452611629512")
    # expand_tweet(conn, tweet_api, "775730673420111872")
    # expand_tweet(conn, tweet_api, "1628567045800591361")
    # expand_tweet(conn, tweet_api, "1860347907615924669")
    # expand_tweet(conn, tweet_api, "1452963540407668745")

    unexpanded_tweets = get_unexpanded_tweet_ids(conn)
    for rest_id in unexpanded_tweets:
        print(f"Processing unexpanded tweet: {rest_id}")

        expanded_tweet_ids = expand_tweet(conn, tweet_api, rest_id)

        if len(expanded_tweet_ids) > 0:
            # after all replies are processed, mark the tweet as expanded.
            mark_tweet_as_expanded(rest_id, conn)
            conn.commit()

            print(f"Expanded tweet: {rest_id}")
            # print("breaking for now")
            # break

        else:
            print(f"Failed to expand tweet: {rest_id}")
            conn.rollback()
            break
