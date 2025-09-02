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
);

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
);

CREATE TABLE IF NOT EXISTS categories (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS tweet_categories (
    tweet_id VARCHAR(20),
    category_id INTEGER,
    PRIMARY KEY (tweet_id, category_id),
    FOREIGN KEY (tweet_id) REFERENCES tweets(rest_id),
    FOREIGN KEY (category_id) REFERENCES categories(id)
);