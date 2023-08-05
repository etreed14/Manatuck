
import GmailTweetScraper2

startString = ''
theme = 'light' # light / dark
tweet_cache = {}

usernames = ['UrbanKaoboy',
            'lukegromen',
            'EpsilonTheory',
            'elonmusk',
            'biancoresearch',
            'FedGuy12',
            'martymakary',
            'crossbordercap',
            'WallStCynic',
            'cryptohayes',
            'Ayjchan',
            'nntaleb',
            'SantiagoAuFund',
            'GuyDealership',
            'VPrasadMDMPH',
            'DiMartinoBooth',
            'HuntBlazer',
            'EconguyRosie',
            'profplum99',
            'htsfhickey',
            'matt_barrie',
            'Jkylebass'
             ]

request = {
    'args': {}
}

startIndex = 0 if startString == '' else usernames.index(startString)
usernames = usernames[startIndex:]
for username in usernames:
    # Set the username in the request object
    request['args']['username'] = username
    # Invoke the fetchTweets function with the dummy request
    tweets, topAccounts, topHashtags, tweet_cache = GmailTweetScraper2.fetchTweets(username) #from request
    # Break if no tweets within 48hrs
    if tweets is None:
        continue
    # Format tweets & get name
    formatted_tweets, actualName, totNumTweets = GmailTweetScraper2.formatTweets(theme, tweet_cache, tweets, topAccounts, topHashtags)
    # Send email with tweets in the body
    recipient_email = 'mark@manatuckhill.com'
    cc_email = ''
    bcc_email = 'temp@manatuckhill.com'
    subject = GmailTweetScraper2.get_email_subject(actualName)
    body_content = '\n'.join(formatted_tweets)  # Combine all tweets into a single string
    background_color = '#14171A' if theme == 'dark' else '#ffffff'
    body = f"<div style='background-color: {background_color};'>{body_content}</div>"
    GmailTweetScraper2.sendEmail(recipient_email, subject, body, totNumTweets, username, cc_email, bcc_email)

# print("Process completed.")
