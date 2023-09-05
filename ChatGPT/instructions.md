Prompt for generating as much of the script as possible. Some bits changed to `[REDACTED]` because I trust OpenAI more than I trust you :) 

A couple of notes:

- I went through four or five fresh conversations with tweaked versions of the prompt before I was happy to start hacking on the result.  Wherever I am explicit about how the script should be implemented (like for parsing the `fetch` command), this was probably because ChatGPT did it in a way that was incorrect, unclear and/or hard to modify if I left it entirely up to the program. In retrospect, this was as much about figuring out what I wanted, as it was about the way I wanted it done. I saved a lot of time and energy not having to write code for dead ends in that exploration. I would have probably abandoned my efforts if I had to write it all by myself.
- The `jq` commands were lifted from an earlier incarnation of the script written in Bash shell. ADA was pretty smart about finding the right JSON paths from the uploaded JSON response.
- The bit about the x-client-transaction-id was inspired by an earlier conversation with ChatGPT where I asked it what its structure was and how to generate new ones.
- I later updated from SQLite to Postgres in a separate conversation. This was about 90% automatic. I had no idea how to do it beforehand, so that probably saved me hours. The entire conversation was pretty great: https://chat.openai.com/share/ca033c98-874b-41c8-9ef3-ceee565945ef
- Stuff like this is gold: https://chat.openai.com/share/c9bc3625-5be2-44ca-88a9-6d5dc1ac42f5


```
I want to write a Python script that downloads my personal Twitter bookmarks and saves them into a SQLite database by emulating the behaviour of a web browser scrolling through the bookmarks page. This can be done by calling the graphQL API of Twitter.

This is the initial fetch, represented as a javascript fetch command:

fetch("https://twitter.com/i/api/graphql/[REDACTED]/Bookmarks?variables=%7B%22count%22%3A20%2C%22includePromotedContent%22%3Atrue%7D&features=%7B%22graphql_timeline_v2_bookmark_timeline%22%3Atrue%2C%22rweb_lists_timeline_redesign_enabled%22%3Atrue%2C%22responsive_web_graphql_exclude_directive_enabled%22%3Atrue%2C%22verified_phone_label_enabled%22%3Afalse%2C%22creator_subscriptions_tweet_preview_api_enabled%22%3Atrue%2C%22responsive_web_graphql_timeline_navigation_enabled%22%3Atrue%2C%22responsive_web_graphql_skip_user_profile_image_extensions_enabled%22%3Afalse%2C%22tweetypie_unmention_optimization_enabled%22%3Atrue%2C%22responsive_web_edit_tweet_api_enabled%22%3Atrue%2C%22graphql_is_translatable_rweb_tweet_is_translatable_enabled%22%3Atrue%2C%22view_counts_everywhere_api_enabled%22%3Atrue%2C%22longform_notetweets_consumption_enabled%22%3Atrue%2C%22responsive_web_twitter_article_tweet_consumption_enabled%22%3Afalse%2C%22tweet_awards_web_tipping_enabled%22%3Afalse%2C%22freedom_of_speech_not_reach_fetch_enabled%22%3Atrue%2C%22standardized_nudges_misinfo%22%3Atrue%2C%22tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled%22%3Atrue%2C%22longform_notetweets_rich_text_read_enabled%22%3Atrue%2C%22longform_notetweets_inline_media_enabled%22%3Atrue%2C%22responsive_web_media_download_video_enabled%22%3Afalse%2C%22responsive_web_enhance_cards_enabled%22%3Afalse%7D", {
  "headers": {
    "accept": "*/*",
    "accept-language": "nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7",
    "authorization": "Bearer [REDACTED]",
    "content-type": "application/json",
    "sec-ch-ua": "\"Chromium\";v=\"116\", \"Not)A;Brand\";v=\"24\", \"Google Chrome\";v=\"116\"",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": "\"Windows\"",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "sec-gpc": "1",
    "x-client-transaction-id": "[REDACTED]",
    "x-client-uuid": "[REDACTED]",
    "x-csrf-token": "[REDACTED]",
    "x-twitter-active-user": "yes",
    "x-twitter-auth-type": "OAuth2Session",
    "x-twitter-client-language": "nl"
  },
  "referrer": "https://twitter.com/i/bookmarks",
  "referrerPolicy": "strict-origin-when-cross-origin",
  "body": null,
  "method": "GET",
  "mode": "cors",
  "credentials": "include"
});

The output of this command is attached as bookmarks.json. This is the first page of a paginated list of bookmarked tweets. It contains a reference to the next page, which can be extracted on the shell as follows:

rawcursor=`jq -r '.data.user.result.timeline_v2.timeline.instructions[0].entries[] | select(.content.cursorType == "Bottom") | .content.value' bookmarks.json | tr -d '='`
cursor=`urlencode -m $rawcursor`

The cursor variable can then be added to the fetch command, resulting in the following:

fetch("https://twitter.com/i/api/graphql/[REDACTED]/Bookmarks?variables=%7B%22count%22%3A20%2C%22cursor%22%3A%22HBbY7q6M8Z2poDEAAA%3D%3D%22%2C%22includePromotedContent%22%3Atrue%7D&features=%7B%22graphql_timeline_v2_bookmark_timeline%22%3Atrue%2C%22rweb_lists_timeline_redesign_enabled%22%3Atrue%2C%22responsive_web_graphql_exclude_directive_enabled%22%3Atrue%2C%22verified_phone_label_enabled%22%3Afalse%2C%22creator_subscriptions_tweet_preview_api_enabled%22%3Atrue%2C%22responsive_web_graphql_timeline_navigation_enabled%22%3Atrue%2C%22responsive_web_graphql_skip_user_profile_image_extensions_enabled%22%3Afalse%2C%22tweetypie_unmention_optimization_enabled%22%3Atrue%2C%22responsive_web_edit_tweet_api_enabled%22%3Atrue%2C%22graphql_is_translatable_rweb_tweet_is_translatable_enabled%22%3Atrue%2C%22view_counts_everywhere_api_enabled%22%3Atrue%2C%22longform_notetweets_consumption_enabled%22%3Atrue%2C%22responsive_web_twitter_article_tweet_consumption_enabled%22%3Afalse%2C%22tweet_awards_web_tipping_enabled%22%3Afalse%2C%22freedom_of_speech_not_reach_fetch_enabled%22%3Atrue%2C%22standardized_nudges_misinfo%22%3Atrue%2C%22tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled%22%3Atrue%2C%22longform_notetweets_rich_text_read_enabled%22%3Atrue%2C%22longform_notetweets_inline_media_enabled%22%3Atrue%2C%22responsive_web_media_download_video_enabled%22%3Afalse%2C%22responsive_web_enhance_cards_enabled%22%3Afalse%7D", {
  "headers": {
    "accept": "*/*",
    "accept-language": "nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7",
    "authorization": "Bearer [REDACTED]",
    "content-type": "application/json",
    "sec-ch-ua": "\"Chromium\";v=\"116\", \"Not)A;Brand\";v=\"24\", \"Google Chrome\";v=\"116\"",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": "\"Windows\"",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "sec-gpc": "1",
    "x-client-transaction-id": "[REDACTED]",
    "x-client-uuid": "[REDACTED]",
    "x-csrf-token": "[REDACTED]",
    "x-twitter-active-user": "yes",
    "x-twitter-auth-type": "OAuth2Session",
    "x-twitter-client-language": "nl"
  },
  "referrer": "https://twitter.com/i/bookmarks",
  "referrerPolicy": "strict-origin-when-cross-origin",
  "body": null,
  "method": "GET",
  "mode": "cors",
  "credentials": "include"
});

The fetch commands are identical except for the cursor variable and the x-client-transaction-id header.

By recursively parsing the response to fetch the cursor value and building the next URL from it, all liked tweets can be retrieved. If the cursor value is empty, that means we are on the last page and can stop working.

The fetch command can be converted into the correct arguments for a call with the Python requests library in the following way:

- strip off the outer fetch(...) method call
- split the remainder on the first occurrence of a comma
- the first half should be used as the URL
- the second half is a JSON object containing multiple fields. One of them is `headers`, which contains a list of subfields which should each be used as one header. In addition, the referrer field should also be used.

To be as representative as possible, change the value of the x-client-transaction-id header to a new random 70-byte sequence for every request, encoded in URLBase64 format.

Save these fields from the response in the database:

sortIndex, rest_id, core.user_results.result.legacy.screen_name, legacy.created_at, legacy.full_text

The script should offer resume functionality. The API returns tweets ordered by the date they were liked, ordered from newer to older. If a fetched tweet is already in the database, stop the script. Use the sortIndex field for this.

Provide the entire script. Don't omit or ellipsize sections. The initial fetch command will be provided as a command-line argument, so that the script can be called like this:

python3 getbookmarks.py 'fetch(...)'
```