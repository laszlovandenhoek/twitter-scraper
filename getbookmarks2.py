import json
from typing import List, Optional
from pathlib import Path
from twitter_openapi_python import TimelineTimelineCursor, Tweet, TwitterApiUtilsResponse, TwitterOpenapiPython, TimelineApiUtilsResponse, TweetApiUtilsData

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

def get_client():
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
    client.api.configuration.host = "http://localhost:8000"
    
    return client

def get_likes(client: TwitterOpenapiPython, user_id: str, cursor: Optional[TimelineTimelineCursor] = None) -> TwitterApiUtilsResponse[TimelineApiUtilsResponse[TweetApiUtilsData]]:
    if cursor is None:
        return client.get_tweet_api().get_likes(user_id=user_id, count=20)
    else:
        return client.get_tweet_api().get_likes(user_id=user_id, count=20, cursor=cursor.value)

def get_bookmarks(client: TwitterOpenapiPython, cursor: Optional[TimelineTimelineCursor] = None) -> TwitterApiUtilsResponse[TimelineApiUtilsResponse[TweetApiUtilsData]]:
    if cursor is None:
        return client.get_tweet_api().get_bookmarks(count=20)
    else:
        return client.get_tweet_api().get_bookmarks(count=20, cursor=cursor.value)

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

def save_index_tweet(tweet: Tweet, conn: psycopg2.extensions.connection):
    with conn.cursor() as c:
        c.execute("""
            INSERT INTO tweet_index (
                rest_id,
                sort_index,
                user_id,
                created_at,
                fetched_at,
                bookmarked,
                liked,
                important,
                archived,
                expanded,
                source_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (tweet.rest_id, tweet.sort_index, tweet.user_id, tweet.created_at, tweet.fetched_at, tweet.bookmarked, tweet.liked, tweet.important, tweet.archived, tweet.expanded, tweet.source_json))
    conn.commit()

def dump_response(response: TwitterApiUtilsResponse[TimelineApiUtilsResponse[TweetApiUtilsData]], file_name: str):
    with open(file_name, "w") as f:
        json.dump(response.model_dump(mode="json"), f, indent=2)



if __name__ == '__main__':
    client = get_client()
    
    print("Getting likes")
    likes_response: TwitterApiUtilsResponse[TimelineApiUtilsResponse[TweetApiUtilsData]] = get_likes(client, "117787606")
    
    # dump_response(likes_response, "likes1.json")

    # print("Getting bookmarks")
    # bookmarks_response: TwitterApiUtilsResponse[TimelineApiUtilsResponse[TweetApiUtilsData]] = client.get_tweet_api().get_bookmarks(count=20)

    # dump_response(bookmarks_response, "bookmarks.json")


    # print("Getting tweet details")
    # tweet_details_response: TwitterApiUtilsResponse[TimelineApiUtilsResponse[TweetApiUtilsData]] = client.get_tweet_api().get_tweet_detail(focal_tweet_id="1933899331813490791")

    # dump_response(tweet_details_response, "tweet_details.json")


    likes: TimelineApiUtilsResponse[TweetApiUtilsData] = likes_response.data
    liked_tweets: List[Tweet] = [x.tweet for x in likes.data]

    for tweet in liked_tweets:
        print(tweet.rest_id)
        
    if (cursor_bottom := likes_response.data.cursor.bottom) is not None:
        print(f"prev cursor: {likes_response.data.cursor.top.value}")
        page0 = get_likes(client, "117787606", likes_response.data.cursor.top)
        with open("likes0.json", "w") as f:
            json.dump(page0.model_dump(mode="json"), f, indent=2)

        print(f"next cursor: {cursor_bottom.value}")
        page2 = get_likes(client, "117787606", cursor_bottom)
        with open("likes2.json", "w") as f:
            json.dump(page2.model_dump(mode="json"), f, indent=2)
        for tweet_thing in page2.data.data:
            print(tweet_thing.tweet.rest_id)
        print(f"prev cursor: {page2.data.cursor.top.value}")
        print(f"next cursor: {page2.data.cursor.bottom.value}")


    # bookmarks: TimelineApiUtilsResponse[TweetApiUtilsData] = bookmarks_response.data
    # thread: TimelineApiUtilsResponse[TweetApiUtilsData] = tweet_details_response.data

    # print(thread.cursor)
    # print(thread.cursor.top)
    # print(thread.cursor.bottom)

    # print(likes_response.data.cursor)
    # print(likes_response.data.cursor.top)
    # print(likes_response.data.cursor.bottom)

    # print(bookmarks_response.data.cursor)
    # print(bookmarks_response.data.cursor.top)
    # print(bookmarks_response.data.cursor.bottom)