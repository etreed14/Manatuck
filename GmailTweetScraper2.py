import os
import re
import json
import pytz
import time
import base64
import tweepy
import smtplib
import sqlite3
import requests
import datetime
from io import BytesIO
from bs4 import BeautifulSoup
from pytz import timezone
from datetime import timedelta
from newspaper import Article
from email.mime.text import MIMEText
from PIL import Image
from reportlab.lib import colors
#from sklearn.ensemble import RandomForestClassifier


# Twitter API credentials
bearerToken = ''


# Connect to SQLite database (or create it if it doesn't exist)
conn = sqlite3.connect('tweet_cache.db')
cursor = conn.cursor()

# Create tweet_cache table if it doesn't exist
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    username TEXT,
    actual_name TEXT,
    profileImageUrl TEXT
)
''')
conn.commit()

cursor.execute('''
CREATE TABLE IF NOT EXISTS tweet_cache (
    tweet_id TEXT PRIMARY KEY,
    username TEXT,
    actual_name TEXT,
    tweet_text TEXT,
    replies INTEGER,
    retweets INTEGER,
    likes INTEGER,
    profileImageUrl TEXT,
    tweetUrl TEXT,
    referencedTweetUrl TEXT,
    hours_since_post INTEGER,
    mins_since_post INTEGER,
    media_urls TEXT,
    in_reply_to_user_id TEXT
)
''')
conn.commit()

def userExists(username):
    cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
    return cursor.fetchone()

def cacheUser(user_data):
    cursor.execute('''
    INSERT OR REPLACE INTO users (user_id, username, actual_name, profileImageUrl)
    VALUES (?, ?, ?, ?)
    ''', (user_data['user_id'], user_data['username'], user_data['actual_name'], user_data['profileImageUrl']))
    conn.commit()
    
def tweetExists(tweet_id, user_id):
    cursor.execute('SELECT * FROM tweets WHERE tweet_id = ? AND user_id = ?', (tweet_id, user_id))
    return cursor.fetchone()
    
def cacheTweet(tweet_data):
    cursor.execute('''
    INSERT OR REPLACE INTO tweets (
        tweet_id, user_id, tweet_text, replies, retweets, likes,
        tweetUrl, referencedTweetUrl, hours_since_post,
        mins_since_post, media_urls, in_reply_to_user_id
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', tweet_data)
    conn.commit()

def create_headers(bearerToken):
    headers = {"Authorization": "Bearer {}".format(bearerToken)}
    return headers

def create_headers(bearerToken):
    headers = {"Authorization": "Bearer {}".format(bearerToken)}
    return headers

def get_article_info(url):
    article = Article(url)
    article.download()
    article.parse()
    return {
        "title": article.title,
        "summary": article.text[:255],  # Get the first 255 characters of the article as a summary
        "image": article.top_image
    }
  
# tweet_cache = {}

def fetchSingleTweet(username, tweet_id):
    cached_tweet = tweetExists(tweet_id, username)
    if cached_tweet:
        return cached_tweet
        
    # Authenticate with Twitter API
    headers = create_headers(bearerToken)
    # Fetch the tweet
    tweet_url = f"https://api.twitter.com/2/tweets/{tweet_id}?tweet.fields=created_at,entities,public_metrics,source,attachments,referenced_tweets&expansions=attachments.media_keys,author_id&media.fields=url&user.fields=id,username,profile_image_url,name"
    response = requests.request("GET", tweet_url, headers=headers)
    print(f"Fetching single tweet with ID: {tweet_id}")
    
    # Check rate limit headers
    remaining = int(response.headers.get('x-rate-limit-remaining', 0))
    reset_time = int(response.headers.get('x-rate-limit-reset', 0))
    # If we're about to hit the rate limit, sleep until the reset time
    if remaining <= 1:  # Adjust this threshold as needed
        now = time.time()
        sleep_duration = reset_time - now
        if sleep_duration > 0:
            now_dt = datetime.datetime.now(pytz.timezone('US/Eastern'))
            minutes, seconds = divmod(sleep_duration, 60)
            resume_time = now_dt + datetime.timedelta(seconds=sleep_duration)
            print(f"Approaching rate limit at {now_dt.strftime('%H:%M:%S')}. Sleeping for {minutes}:{seconds} until {resume_time.strftime('%H:%M:%S')}")
            time.sleep(sleep_duration)
    if response.status_code == 429:
        reset_time = int(response.headers.get('x-rate-limit-reset')) # Get reset time from headers
        now = datetime.datetime.now(pytz.timezone('US/Eastern'))
        sleep_duration = reset_time - int(time.time()) # Calculate sleep duration
        minutes, seconds = divmod(sleep_duration, 60) # Convert sleep dur. to mins & secs
        resume_time = now + datetime.timedelta(seconds=sleep_duration)
        print(f"Rate limit exceeded at {datetime.datetime.now(pytz.timezone('US/Eastern')).strftime('%H:%M:%S')}. Sleeping for 0:{minutes}:{seconds} until {resume_time.strftime('%H:%M:%S')}")
        time.sleep(sleep_duration)
        response = requests.request("GET", tweet_url, headers=headers)  # Retry request
    if response.status_code != 200:
        print(f"Could not fetch tweet with ID {tweet_id}: {response.status_code}, {response.text}")
        return None
        
    tweet = response.json()
    if 'data' not in tweet:
        print(f"Unexpected response format for tweet with ID {tweet_id}")
        return None

    # Get the author's data from the 'includes' section
    user = next((user for user in tweet['includes']['users'] if user['id'] == tweet['data']['author_id']), None)

    # Get the author's data
    actual_name = user['name']
    username = user['username']
    profileImageUrl = user['profile_image_url']

    # Get the tweet's data
    tweet_text = tweet['data']['text']
    replies = tweet['data']['public_metrics']['reply_count']
    retweets = tweet['data']['public_metrics']['retweet_count']
    likes = tweet['data']['public_metrics']['like_count']
    now = datetime.datetime.now(pytz.utc)
    tweet_time = datetime.datetime.strptime(tweet['data']['created_at'], "%Y-%m-%dT%H:%M:%S.%fZ")
    tweet_time = tweet_time.replace(tzinfo=pytz.UTC)  # Attach UTC timezone
    hours_since_post = int((now - tweet_time).total_seconds() // 3600)
    mins_since_post = int((now - tweet_time).total_seconds() // 60)
    tweetUrl = f"https://twitter.com/{username}/status/{tweet['data']['id']}"
    media_urls = []
    if 'attachments' in tweet['data'] and 'media_keys' in tweet['data']['attachments']:
        if 'includes' in tweet and 'media' in tweet['includes']:
            for media_key in tweet['data']['attachments']['media_keys']:
                for media in tweet['includes']['media']:
                    if media['media_key'] == media_key and 'url' in media:
                        media_urls.append(media['url'])

    # Check if this tweet is a reply or a retweet
    referencedTweetUrl = None
    if 'referenced_tweets' in tweet['includes']:
        # This is a reply or a retweet
        for ref_tweet in tweet['includes']['referenced_tweets']:
            # Get the author's ID of the referenced tweet
            referenced_author_id = ref_tweet['author_id']
            # Get the author's username of the referenced tweet
            referenced_username = None
            for user in tweet['includes']['users']:
                if user['id'] == referenced_author_id:
                    referenced_username = user['username']
                    break
            # Create the URL for the referenced tweet
            referencedTweetUrl = f"https://twitter.com/{referenced_username}/status/{ref_tweet['id']}"

    tweet_data = (actual_name, username, tweet_text, replies, retweets, likes, profileImageUrl, tweetUrl, referencedTweetUrl, hours_since_post, mins_since_post, media_urls, tweet['data'].get('in_reply_to_user_id', None))
    
    # Cache the tweet's data
    tweet_data_db = (
        tweet_id, user['id'], tweet_text, replies, retweets, likes,
        tweetUrl, referencedTweetUrl, hours_since_post,
        mins_since_post, ','.join(media_urls), tweet['data'].get('in_reply_to_user_id', None)
    )
    cacheTweet(tweet_data_db)
    # Cache the user's data
    user_data_db = (user['id'], username, user['name'], user['profile_image_url'])
    cacheUser(user_data_db)
    
    return tweet_data
  
def fetchTweets(username, max_tweets=5, max_hrs_ago=2):
    # Authenticate with Twitter API
    headers = create_headers(bearerToken)

    user = userExists(username)
    if user is None:
        # Fetch the user's data
        user_url = f"https://api.twitter.com/2/users/by/username/{username}?user.fields=id,profile_image_url,name"
        response = requests.request("GET", user_url, headers=headers)
        print(f"Fetching details for user: {username}")
        # Check rate limit headers
        remaining = int(response.headers.get('x-rate-limit-remaining', 0))
        reset_time = int(response.headers.get('x-rate-limit-reset', 0))
        # If we're about to hit the rate limit, sleep until the reset time
        if remaining <= 1:  # Adjust this threshold as needed
            now = time.time()
            sleep_duration = reset_time - now
            if sleep_duration > 0:
                now_dt = datetime.datetime.now(pytz.timezone('US/Eastern'))
                minutes, seconds = divmod(sleep_duration, 60)
                resume_time = now_dt + datetime.timedelta(seconds=sleep_duration)
                print(f"Approaching rate limit at {now_dt.strftime('%H:%M:%S')}. Sleeping for {minutes}:{seconds} until {resume_time.strftime('%H:%M:%S')}")
                time.sleep(sleep_duration)
        if response.status_code == 429:
            reset_time = int(response.headers.get('x-rate-limit-reset')) # Get reset time from headers
            now = datetime.datetime.now(pytz.timezone('US/Eastern'))
            sleep_duration = reset_time - int(time.time()) # Calculate sleep duration
            minutes, seconds = divmod(sleep_duration, 60) # Convert sleep dur. to mins & secs
            resume_time = now + datetime.timedelta(seconds=sleep_duration)
            print(f"Rate limit exceeded at {datetime.datetime.now(pytz.timezone('US/Eastern')).strftime('%H:%M:%S')}. Sleeping for 0:{minutes}:{seconds} until {resume_time.strftime('%H:%M:%S')}")
            time.sleep(sleep_duration)
            response = requests.request("GET", user_url, headers=headers)  # Retry request
        if response.status_code != 200:
            raise Exception(f"Request returned an error: {response.status_code}, {response.text}")
        #print("Status code:", response.status_code)
        #print("Response text:", response.text)
        try:
            user = response.json()
        except json.JSONDecodeError:
            print("Failed to parse JSON response")
            user = None
        # Get the user's data
        actual_name = user['data']['name']
        profileImageUrl = user['data']['profile_image_url']
        # Define the user_data dictionary
        user_data = {
            'user_id': user['data']['id'],
            'username': username,
            'actual_name': actual_name,
            'profileImageUrl': profileImageUrl
        }
        cacheUser(user_data) # Cache the user's data
    else:
        user_data = user

    # Fetch the tweets
    tweets_url = f"https://api.twitter.com/2/users/{user['data']['id']}/tweets?max_results={max_tweets}&tweet.fields=created_at,entities,public_metrics,source,attachments,referenced_tweets&expansions=attachments.media_keys,author_id&media.fields=url"
    response = requests.request("GET", tweets_url, headers=headers)
    print(f"Fetching tweets for user: {username}")
    
    # Check rate limit headers
    remaining = int(response.headers.get('x-rate-limit-remaining', 0))
    reset_time = int(response.headers.get('x-rate-limit-reset', 0))
    # If we're about to hit the rate limit, sleep until the reset time
    if remaining <= 1:  # Adjust this threshold as needed
        now = time.time()
        sleep_duration = reset_time - now
        if sleep_duration > 0:
            now_dt = datetime.datetime.now(pytz.timezone('US/Eastern'))
            minutes, seconds = divmod(sleep_duration, 60)
            resume_time = now_dt + datetime.timedelta(seconds=sleep_duration)
            print(f"Approaching rate limit at {now_dt.strftime('%H:%M:%S')}. Sleeping for {minutes}:{seconds} until {resume_time.strftime('%H:%M:%S')}")
            time.sleep(sleep_duration)
    if response.status_code == 429:
        reset_time = int(response.headers.get('x-rate-limit-reset')) # Get reset time from headers
        now = datetime.datetime.now(pytz.timezone('US/Eastern'))
        sleep_duration = reset_time - int(time.time()) # Calculate sleep duration
        minutes, seconds = divmod(sleep_duration, 60) # Convert sleep dur. to mins & secs
        resume_time = now + datetime.timedelta(seconds=sleep_duration)
        print(f"Rate limit exceeded at {datetime.datetime.now(pytz.timezone('US/Eastern')).strftime('%H:%M:%S')}. Sleeping for 0:{minutes}:{seconds} until {resume_time.strftime('%H:%M:%S')}")
        time.sleep(sleep_duration)
        response = requests.request("GET", user_url, headers=headers)  # Retry request
    if response.status_code != 200:
        raise Exception(f"Request returned an error: {response.status_code}, {response.text}")
    
    tweets = response.json()

    # Process the tweets
    tweet_data = []
    accounts = {}
    hashtags = {}
    tweetCount = 0
    for tweet in tweets['data']:
        # Get the tweet's data
        tweet_id = int(tweet['id'])
        # Check if the tweet exists in the cache
        cached_tweet = tweetExists(tweet_id, username)
        if cached_tweet:
            tweet_data.append(cached_tweet)
        else:
            tweet_text = tweet['text']
            replies = tweet['public_metrics']['reply_count']
            retweets = tweet['public_metrics']['retweet_count']
            likes = tweet['public_metrics']['like_count']
            now = datetime.datetime.now(pytz.utc)
            tweet_time = datetime.datetime.strptime(tweet['created_at'], "%Y-%m-%dT%H:%M:%S.%fZ")
            tweet_time = tweet_time.replace(tzinfo=pytz.UTC)  # Attach UTC timezone
            hours_since_post = int((now - tweet_time).total_seconds() // 3600)
            mins_since_post = int((now - tweet_time).total_seconds() // 60)
            tweetUrl = f"https://twitter.com/{username}/status/{tweet['id']}"
            media_urls = []
            response_data = response.json()  # Parse the 'Response' object into a dictionary

            # Stop if the tweet is older than max_hrs_ago
            if mins_since_post > max_hrs_ago*60:
                break # Exit the loop

            if 'attachments' in tweet and 'media_keys' in tweet['attachments']:
                for media_key in tweet['attachments']['media_keys']:
                    for media in response_data['includes']['media']:
                        if media['media_key'] == media_key:
                            if 'url' in media:
                                media_urls.append(media['url'])

            # Check if this tweet is a reply or a retweet
            referencedTweetUrl = None
            if 'referenced_tweets' in tweet:
                # This is a reply or a retweet
                referencedTweetUrl = f"https://twitter.com/{tweet['referenced_tweets'][0]['id']}"
             
            tweet_data.append((actual_name, username, tweet_text, replies, retweets, likes, profileImageUrl, tweetUrl, referencedTweetUrl, hours_since_post, mins_since_post, media_urls, tweet.get('in_reply_to_user_id', None)))
            cacheTweet(tweet_data[-1])

        tweetCount += 1
        # Collect statistics
        if 'entities' in tweet:
            if 'mentions' in tweet['entities']:
                for mention in tweet['entities']['mentions']:
                    if mention['username'] not in accounts:
                        accounts[mention['username']] = 0
                    accounts[mention['username']] += 1
            if 'hashtags' in tweet['entities']:
                for hashtag in tweet['entities']['hashtags']:
                    if hashtag['tag'] not in hashtags:
                        hashtags[hashtag['tag']] = 0
                    hashtags[hashtag['tag']] += 1
        
    # Sort the dictionaries by value in descending order and take the first 5 items
    topAccounts = sorted(accounts.items(), key=lambda x: x[1], reverse=True)
    topHashtags = sorted(hashtags.items(), key=lambda x: x[1], reverse=True)

    # Filter out accounts and hashtags that only occur once
    topAccounts = [item for item in topAccounts if item[1] > 1 and item[0].lower() != '@' + username.lower()][:5]
    topHashtags = [item for item in topHashtags if item[1] > 1][:5]

    if not tweet_data:
        tweet_data = None
        print(f"Skipping {username}, no tweets within last {max_hrs_ago}hrs")

    return tweet_data, topAccounts, topHashtags
    

def formatSingleTweet(theme, actual_name, username, tweet_text, replies, retweets, likes, profileImageUrl, tweetUrl, referencedTweetUrl, hours_since_post, mins_since_post, media_urls, *args, in_thread=False):

    tBlack = '#14171A'
    dark_grey = '#657786'
    light_grey = '#AAB8C2'
    xlight_grey = '#E1E8ED'
    xxlight_grey = '#F5F8FA'
    tWhite = '#ffffff'
    tBlue = '#1DA1F2'
    if theme == 'dark':
        background_color = tBlack
        text_color = '#e6e9ea'
        grey_color = xlight_grey # '#686d71'
        blue_color = '#1d9bf0'
        border_color = dark_grey
    else:
        background_color = tWhite
        text_color = '#000000'
        grey_color = dark_grey
        blue_color = tBlue
        border_color = light_grey

    # Wrap hashtags in tweet text with a span to change color
    if isinstance(tweet_text, str):
        tweet_text = re.sub(r'(#[\w\d]+)', fr'<span style="color: {blue_color};">\1</span>', tweet_text)

    # Check if tweet starts with a username
    if re.match(r'^@\w+', tweet_text) and isinstance(tweet_text, str):
        tweet_text = f'<span style="color: {grey_color};">Replying to </span>' + tweet_text
                
    # Wrap usernames in tweet text with a span to change color, excluding the username of the person who tweeted
    if isinstance(tweet_text, str):
        tweet_text = re.sub(r'(@\w+)', lambda m: f'<span style="color: {blue_color};">{m.group(0)}</span>' if m.group(0) != '@' + username else m.group(0), tweet_text)

    # Check if tweet starts with "RT"
    if tweet_text.startswith('RT'):
        tweet_text = f'<span style="color: {grey_color};">' + tweet_text[:2] + '</span>' + tweet_text[2:]

    # Check if hours_since_post is more than 48
    hours_since_post = int(hours_since_post)
    mins_since_post = int(mins_since_post)
    if hours_since_post < 1:
        post_time = f"{int(mins_since_post)}m"
    elif hours_since_post < 48:
        post_time = f"{int(hours_since_post)}h"
    else:
        post_time = datetime.datetime.now() - datetime.timedelta(hours=hours_since_post)
        post_time = post_time.strftime('%b %d')
        
    # Add 'In Thread' label if the tweet is part of a thread
    if in_thread:
        post_time = f"{post_time} &middot; In Thread"

    # Download and resize images
    images = ''
    for i, url in enumerate(media_urls):
        response = requests.get(url)
        try:
            img = Image.open(BytesIO(response.content))
            if img.mode == 'P':  # Check if image is palette mode
                img = img.convert('RGBA')  # Convert to RGBA
            image = img.convert("RGB")
            image.thumbnail((450, 450))
            buffered = BytesIO()
            image.save(buffered, format="JPEG")
            img_str = base64.b64encode(buffered.getvalue()).decode()
            images += f'<img src="data:image/jpeg;base64,{img_str}" alt="Image from tweet" style="border-radius: 15px;"><br>'
        except Exception as e:
            print(f"Failed to process image at {url}: {e}")

    formatted_referenced_tweet = f"""
    <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap" rel="stylesheet">
    <table style='max-width: auto; margin: 2px auto; border: 1px solid {border_color}; border-radius: 15px; padding: 0px; font-family: Roboto, sans-serif; font-size: 15px; line-height: 1.2; background-color: {background_color};'>
        <tr>
            <td style='width: 100%; vertical-align: top; padding-top: 0px; padding-left: 4px;'>
                <table>
                    <tr>
                        <td style='width: 25px; vertical-align: top'>
                            <img src='{profileImageUrl}' style='width: 25px !important; height: 25px !important; border-radius: 50%;' width='25' height='25'>
                        </td>
                        <td style='vertical-align: top'>
                            <span style='font-weight: bold; color: {text_color}; padding-left: 10px'>{actual_name}</span>
                            <span style='color: {grey_color};'> @{username} <span style='font-size: 15px;'>&middot</span>
                            <span style='color: {grey_color};'> {post_time}</span>
                        </td>
                    </tr>
                </table>
                <div style='color: {text_color}; margin-top: 0px;'>{tweet_text}</div>
                <div style='margin-top: 6px;'>{images}</div>
            </td>
        </tr>
    </table>
    """

    # Open the file in append mode, create new file to put the text of the tweets
    with open(f'/Users/ethanreed/manatuckScraper/TweetTexts/{username}_tweets.txt', 'a') as f:
        if isinstance(tweet_text, str):
            # Write the tweet to the file
            tweet_text = re.sub('<[^<]+?>', '', formatted_referenced_tweet)
            f.write(f'{tweet_text}\n')
            # Write a separator for readability
            f.write('\n') # add line break after each tweet

    return formatted_referenced_tweet


def formatTweets(theme, tweets, topAccounts, topHashtags):

    tBlack = '#14171A'
    dark_grey = '#657786'
    light_grey = '#AAB8C2'
    xlight_grey = '#E1E8ED'
    xxlight_grey = '#F5F8FA'
    tWhite = '#ffffff'
    tBlue = '#1DA1F2'
    if theme == 'dark':
        background_color = tBlack
        text_color = '#e6e9ea'
        grey_color = xlight_grey # '#686d71'
        blue_color = '#1d9bf0'
        border_color = dark_grey
    else:
        background_color = tWhite
        text_color = '#000000'
        grey_color = dark_grey
        blue_color = tBlue
        border_color = light_grey
    totNumTweets = {"tweets": 0, "ref_tweets": 0}

    # Group tweets by thread_id
    grouped_tweets = {}
    username1 = tweets[0][1]
    for tweet in tweets:
        in_reply_to_user_id = tweet[12]  # Assuming this is where you're storing in_reply_to_user_id
        if in_reply_to_user_id not in grouped_tweets:
            grouped_tweets[in_reply_to_user_id] = []
        grouped_tweets[in_reply_to_user_id].append(tweet)

    # Format top accounts and hashtags
    formatted_top_accounts = ', '.join(f'{account} ({count}x)' for account, count in topAccounts if count > 1 and username1.lower() not in account.lower())
    formatted_top_hashtags = ', '.join(f'{hashtag} ({count}x)' for hashtag, count in topHashtags if count > 1)
    
    # Sort each group of tweets by timestamp
    for thread_id, group in grouped_tweets.items():
        group.sort(key=lambda tweet: (tweet[9], tweet[10]))  # Sorting by hours_since_post and mins_since_post
        
    plain_text_tweets = []
    formatted_tweets = []
    formatted_tweets.append(f'<div style="margin-top: -5px; margin-bottom: 15px; color: {grey_color};">@{username1} 𝕏 daily tweets &middot; 👀 is link to tweet, 💬 is link to referenced material if any</div>')
    if len(formatted_top_accounts) > 0:
        formatted_top_accounts = ', '.join([f'<span style="color: {blue_color};">{account}</span><span style="color: {grey_color};"> ({count}x)</span>' for account, count in topAccounts])
        formatted_tweets.append(f'<div style="margin-top: 15px; color: {text_color};">Top Accounts : {formatted_top_accounts}</div>')
    if len(formatted_top_hashtags) > 0:
        formatted_top_hashtags = ', '.join([f'<span style="color: {blue_color};">{hashtag}</span><span style="color: {grey_color};"> ({count}x)</span>' for hashtag, count in topHashtags])
        formatted_tweets.append(f'<div style="margin-top: 5px; color: {text_color};">Top Hashtags: {formatted_top_hashtags}</div>')
    
    directory = '/Users/ethanreed/manatuckScraper/TweetTexts'
    os.makedirs(directory, exist_ok=True)
    
    processed_tweets = set()
    for thread_id, group in grouped_tweets.items():
        for i in range(len(group)):
            actual_name, username, tweet_text, replies, retweets, likes, profileImageUrl, tweetUrl, referencedTweetUrl, hours_since_post, mins_since_post, media_urls, in_reply_to_user_id = group[i]
            
            plain_text_tweets.append(tweet_text)
            
            if tweetUrl in processed_tweets:
                continue
            processed_tweets.add(tweetUrl)
            # Add formatting here based on your requirements

            # Wrap hashtags in tweet text with a span to change color
            if isinstance(tweet_text, str):
                tweet_text = re.sub(r'(#[\w\d]+)', r'<span style="color: #1DA1F2;">\1</span>', tweet_text)

            # Check if tweet starts with a username
            if re.match(r'^@\w+', tweet_text):
                tweet_text = '<span style="color: #657786;">Replying to </span>' + tweet_text
                
            # Wrap usernames in tweet text with a span to change color, excluding the username of the person who tweeted
            if isinstance(tweet_text, str):
                tweet_text = re.sub(r'(@\w+)', lambda m: '<span style="color: #1DA1F2;">' + m.group(0) + '</span>' if m.group(0) != '@' + username else m.group(0), tweet_text)

            # Check if tweet starts with "RT"
            if tweet_text.startswith('RT'):
                tweet_text = '<span style="color: #657786;">' + tweet_text[:2] + '</span>' + tweet_text[2:]

            # Check if hours_since_post is more than 48
            hours_since_post = int(hours_since_post)
            mins_since_post = int(mins_since_post)
            if hours_since_post < 1:
                post_time = f"{int(mins_since_post)}m"
            elif hours_since_post <= 48:
                post_time = f"{int(hours_since_post)}h"
            else:
                post_time = datetime.datetime.now() - datetime.timedelta(hours=hours_since_post)
                post_time = post_time.strftime('%b %d')
            
            if thread_id is not None:
                post_time = f"{post_time} &middot; In Thread"
                if username.lower() == in_reply_to_user_id.lower() and hours_since_post <= 48:
                    referencedTweetUrl = None
                
            # Create links for the individual tweet and the referenced tweet
            tweet_link = f'<a href="{tweetUrl}" style="color: #1DA1F2;">👀</a>'
            referenced_tweet_link = ''
            referenced_tweet = ''

            # fetchSingleTweet if referencedTweetUrl is present
            if referencedTweetUrl is not None and referencedTweetUrl != '':
                referenced_tweet_link = f'<a href="{referencedTweetUrl}" style="color: #1DA1F2;">💬</a>'
                # Extract the tweet ID from the URL
                referenced_tweet_id = referencedTweetUrl.split('/')[-1]
                
                # Check if the referenced tweet's data is cached in the database
                referenced_tweet_data = tweetExists(referenced_tweet_id, user_id)
                if not referenced_tweet_data:
                    # If not cached, fetch the referenced tweet's data
                    referenced_tweet_data = fetchSingleTweet(username, referenced_tweet_id)
                
                if referenced_tweet_data is not None:
                    # Format the referenced tweet's data
                    referenced_tweet = formatSingleTweet(theme, *referenced_tweet_data)
                    # Add the formatted referenced tweet to the main tweet's text
                    referenced_tweet = f'<div style="margin-left: 0px;">{referenced_tweet}</div>' #from 51px to remove indent
                    # Remove the referenced tweet link from the tweet text
                    tweet_text = tweet_text.replace(referencedTweetUrl, '')
                    totNumTweets["ref_tweets"] += 1 # add counter
                else:
                    referenced_tweet = ''
                if "twitter.com" not in referencedTweetUrl and "t.co" not in referencedTweetUrl:
                    try:
                        info = get_article_info(referencedTweetUrl)
                        link_preview_html = f"""
                        <div>
                            <h1>{info['title']}</h1>
                            <p>{info['summary']}</p>
                            <img src="{info['image']}" alt="Preview image">
                        </div>
                        """
                        referenced_tweet += link_preview_html
                    except Exception as e:
                        print(f"Failed to generate preview for {referencedTweetUrl}: {e}")

            # Download and resize images
            images = ''
            for i, url in enumerate(media_urls):
                response = requests.get(url)
                try:
                    img = Image.open(BytesIO(response.content))
                    if img.mode == 'P':  # Check if image is palette mode
                        img = img.convert('RGBA')  # Convert to RGBA
                    image = img.convert("RGB")
                    image.thumbnail((450, 450))
                    buffered = BytesIO()
                    image.save(buffered, format="JPEG")
                    img_str = base64.b64encode(buffered.getvalue()).decode()
                    images += f'<img src="data:image/jpeg;base64,{img_str}" alt="Image from tweet" style="border-radius: 15px;"><br>'
                except Exception as e:
                    print(f"Failed to process image at {url}: {e}")

            formatted_tweet = f"""
            <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap" rel="stylesheet">
            <table style='max-width: auto; margin: 10px auto; padding: 0px; font-family: Roboto, sans-serif; font-size: 15px; line-height: 1.2; background-color: {background_color};'>
                <tr>
                    <td style='width: 22px; vertical-align: top; padding-top: 22px; padding-left: 8px;'>
                        <img src='{profileImageUrl}' style='width: 41px !important; height: 41px !important; border-radius: 50%;' width='41' height='41'>
                    </td>
                    <td style='width: 100%; vertical-align: top; padding-top: 24px; padding-left: 10px;'>
                        <span style='font-weight: bold; color: {text_color};'>{actual_name}</span>
                        <span style='color: {grey_color};'> @{username} <span style='font-size: 15px;'>&middot</span>
                        <span style='color: {grey_color};'> {post_time}</span>
                        <div style='color: {text_color}; margin-top: 8px;'>{tweet_text}</div>
                        <div style='margin-top: 0px;'>{images}</div>
                        <div style='margin-top: 0px; margin-bottom: 2px;'>{referenced_tweet}</div>
                        <div style='margin-top: 0px; color: {grey_color}; font-size: 13px;'>
                            {tweet_link} &nbsp;&nbsp;&nbsp;
                            <span style='color: {grey_color};'>🔁 {retweets}</span> &nbsp;
                            <span style='color: {grey_color};'>❤️ {likes} </span> &nbsp;&nbsp;
                            {referenced_tweet_link}
                        </div>
                    </td>
                </tr>
            </table>
            """

            formatted_tweets.append(formatted_tweet)
            totNumTweets["tweets"] += 1
            # Open the file in append mode, create new file to put the text of the tweets
            with open(f'{directory}/{username1}_tweets.txt', 'a') as f:
                # Write each tweet to the file
                for tweet_text in plain_text_tweets:
                    f.write(f'{tweet_text}\n')
                    # Write a separator for readability
                    f.write('\n') # add line break after each tweet

    return formatted_tweets, actual_name, totNumTweets

    
def sendEmail(recipient_email, subject, body, totNumTweets, username, cc_email=None, bcc_email=None):
    # Set up email parameters
    sender_email = "@gmail.com"
    smtp_server = "smtp.gmail.com"
    smtp_port = 587
    smtp_username = "@gmail.com"
    smtp_password = ""

    # Create email message
    msg = MIMEText(body, 'html')
    msg['Subject'] = subject
    msg['From'] = sender_email
    all_recipients = [recipient_email]
    if cc_email:
        all_recipients.append(cc_email)
    if bcc_email:
        all_recipients.append(bcc_email)
    msg['To'] = ', '.join(all_recipients)  # Include all recipients in the "To" field

    # Define eastern timezone & get current time
    eastern = pytz.timezone('US/Eastern')
    easternTime = datetime.datetime.now(eastern)

    # Send email
    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            to_addrs = [recipient_email]
            if cc_email:
                to_addrs.append(cc_email)
            # Do not include BCC in to_addrs when calling sendmail
            server.sendmail(sender_email, to_addrs, msg.as_string())
            server.sendmail(sender_email, 'temp@manatuckhill.com', msg.as_string())
        print(f"@{username} -> {recipient_email} - {easternTime.strftime('%H:%M:%S')} email sent successfully w/ {totNumTweets['tweets']} tweets & {totNumTweets['ref_tweets']} refs")
        return True
    except Exception as e:
        print(f"Error sending email for @{username} to {recipient_email}: {str(e)}")
        return False

        
def get_email_subject(actualName):
    today = datetime.date.today()
    subject_date = f"{today.month}/{today.day}"
    email_subject = f"{actualName} {subject_date}"
    return email_subject

