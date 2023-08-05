#from flask import Flask
import json
import GmailTweetScraper
import GmailTweetScraper2

#app = Flask(__name__)
theme = 'light' # light / dark
tweet_cache = {}

# List of Twitter Usernames (@'Username')
usernames = ['Jkylebass'
             ]

request = { # Create dummy request object
    'args': {}
}

for username in usernames:
    # Set the username in the request object
    request['args']['username'] = username
    # Invoke the fetchTweets function with the dummy request
    tweets, topAccounts, topHashtags, tweet_cache = GmailTweetScraper2.fetchTweets(username, tweet_cache) #from request
    # Break if no tweets within 48hrs
    if tweets is None:
        continue
    # Format tweets & get name
    formatted_tweets, actualName, totNumTweets = GmailTweetScraper2.formatTweets(theme, tweet_cache, tweets, topAccounts, topHashtags)
    # Send email with tweets in the body
    recipient_email = 'temp@manatuckhill.com'
    cc_email = ''
    bcc_email = ''
    subject = GmailTweetScraper2.get_email_subject(actualName)
    body_content = '\n'.join(formatted_tweets)  # Combine all tweets into a single string
    background_color = '#14171A' if theme == 'dark' else '#ffffff'
    body = f"<div style='background-color: {background_color};'>{body_content}</div>"
    GmailTweetScraper2.sendEmail(recipient_email, subject, body, totNumTweets, username, cc_email, bcc_email)

# print("Process completed.")

#    body = f"""
#    <table style="background-color: {background_color}; width: 100%; height: 100%;">
#        <tr>
#            <td>
#                {body_content}
#            </td>
#        </tr>
#    </table>
#    """

#    # Save tweets, topAccounts, and topHashtags to JSON files
#    with open('sampleTweets.json', 'w') as f:
#        json.dump(tweets, f)
#    with open('sampleTopAccounts.json', 'w') as f:
#        json.dump(topAccounts, f)
#    with open('sampleTopHashtags.json', 'w') as f:
#        json.dump(topHashtags, f)
