CREATE TABLE IF NOT EXISTS fetches (
    id SERIAL PRIMARY KEY,
    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMP,
    is_likes BOOLEAN NOT NULL,
    is_bookmarks BOOLEAN NOT NULL,
    start_cursor TEXT NOT NULL,
    last_cursor TEXT
);

CREATE TABLE IF NOT EXISTS tweet_index (
    rest_id VARCHAR(20) PRIMARY KEY,
    conversation_id VARCHAR(20) NOT NULL,
    sort_index VARCHAR(20) NOT NULL,
    user_id TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    fetched_at TIMESTAMP NOT NULL DEFAULT NOW(),
    bookmarked BOOLEAN NOT NULL DEFAULT False,
    liked BOOLEAN NOT NULL DEFAULT False,
    important BOOLEAN NOT NULL DEFAULT False,
    archived BOOLEAN NOT NULL DEFAULT False,
    expanded BOOLEAN NOT NULL DEFAULT False,
    source_json JSONB NOT NULL,
    fetch_id INTEGER NOT NULL,
    FOREIGN KEY (fetch_id) REFERENCES fetches(id)
);

CREATE TABLE IF NOT EXISTS tweets (
    anchor_rest_id VARCHAR(20) NOT NULL,
    conversation_id VARCHAR(20) NOT NULL,
    rest_id VARCHAR(20) NOT NULL,
    sort_index VARCHAR(20) NOT NULL,
    user_id TEXT NOT NULL,
    screen_name TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    fetched_at TIMESTAMP NOT NULL DEFAULT NOW(),
    full_text TEXT NOT NULL,
    bookmarked BOOLEAN NOT NULL DEFAULT False,
    liked BOOLEAN NOT NULL DEFAULT False,
    source_json JSONB NOT NULL,
    PRIMARY KEY (anchor_rest_id, rest_id),
    FOREIGN KEY (anchor_rest_id) REFERENCES tweet_index(rest_id)
);

CREATE TABLE IF NOT EXISTS retweets (
    anchor_rest_id VARCHAR(20) NOT NULL,
    rest_id VARCHAR(20) NOT NULL,
    retweet_anchor_rest_id VARCHAR(20) NOT NULL,
    retweet_rest_id VARCHAR(20) NOT NULL,
    is_quote BOOLEAN NOT NULL,
    PRIMARY KEY (anchor_rest_id, rest_id, retweet_anchor_rest_id, retweet_rest_id),
    FOREIGN KEY (anchor_rest_id, rest_id) REFERENCES tweets(anchor_rest_id, rest_id),
    FOREIGN KEY (retweet_anchor_rest_id, retweet_rest_id) REFERENCES tweets(anchor_rest_id, rest_id)
);

CREATE TABLE IF NOT EXISTS categories (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS tweet_categories (
    anchor_rest_id VARCHAR(20),
    tweet_id VARCHAR(20),
    category_id INTEGER,
    PRIMARY KEY (anchor_rest_id, tweet_id, category_id),
    FOREIGN KEY (anchor_rest_id, tweet_id) REFERENCES tweets(anchor_rest_id, rest_id),
    FOREIGN KEY (category_id) REFERENCES categories(id)
);