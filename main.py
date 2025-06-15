import tweepy
import feedparser
import requests
import schedule
import time
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# X API Credentials
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
ACCESS_TOKEN_SECRET = os.getenv("ACCESS_TOKEN_SECRET")
BEARER_TOKEN = os.getenv("BEARER_TOKEN")

# Initialize X API client
client = tweepy.Client(
    bearer_token=BEARER_TOKEN,
    consumer_key=API_KEY,
    consumer_secret=API_SECRET,
    access_token=ACCESS_TOKEN,
    access_token_secret=ACCESS_TOKEN_SECRET
)

# RSS feed URLs
RSS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed"
]

# File to store posted articles
POSTED_ARTICLES_FILE = "posted_articles.txt"

# GPT-4o API details
GPT_API_URL = "https://api.a4f.co/v1/chat/completions"
GPT_API_KEY = os.getenv("GPT_API_KEY")

def load_posted_articles():
    """Load previously posted article titles/URLs from file, remove entries older than 30 days."""
    if not os.path.exists(POSTED_ARTICLES_FILE):
        return set()
    
    cutoff_date = datetime.now(timezone.utc).date() - timedelta(days=30)
    kept_articles = set()
    new_lines = []
    
    with open(POSTED_ARTICLES_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if ":" in line:
                date_str, article_id = line.split(":", 1)
                try:
                    post_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                    if post_date < cutoff_date:
                        print(f"Removing old entry: {line}")
                        continue
                    kept_articles.add(article_id)
                    new_lines.append(line + "\n")
                except ValueError:
                    print(f"Malformed date in entry, treating as old: {line}")
                    continue
            else:
                print(f"Old format entry, removing: {line}")
                continue
    
    with open(POSTED_ARTICLES_FILE, "w") as f:
        f.writelines(new_lines)
    
    return kept_articles

def save_posted_article(article_id):
    """Save posted article title/URL to file with the current date."""
    post_date = datetime.now(timezone.utc).date()
    with open(POSTED_ARTICLES_FILE, "a") as f:
        f.write(f"{post_date}:{article_id}\n")

def fetch_rss_news():
    """Fetch news from RSS feeds, filter by current date, and sort by pubDate (oldest first)."""
    today = datetime.now(timezone.utc).date()
    news_items = []
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            print(f"Fetched {len(feed.entries)} articles from {feed_url}")
            for entry in feed.entries:
                pub_date_struct = entry.get("published_parsed", entry.get("updated_parsed", None))
                if not pub_date_struct:
                    print(f"No pubDate for article: {entry.get('title', 'No title')}")
                    continue
                pub_date = datetime(*pub_date_struct[:6], tzinfo=timezone.utc)
                if pub_date.date() != today:
                    print(f"Article not from today: {entry.get('title', 'No title')} (Date: {pub_date.date()})")
                    continue
                print(f"Found article from today: {entry.get('title', 'No title')}")
                news_items.append({
                    "title": entry.get("title", "No title"),
                    "link": entry.get("link", ""),
                    "summary": entry.get("summary", entry.get("description", "")),
                    "pub_date": pub_date
                })
        except Exception as e:
            print(f"Error fetching RSS feed {feed_url}: {e}")
    
    news_items.sort(key=lambda x: x["pub_date"], reverse=False)
    return news_items

def format_tweet_with_gpt(news_title, news_summary):
    """Use GPT-4o API to summarize and format news into a tweet."""
    prompt = f"""
    {news_title} - {news_summary}

    Summarize this news into a single X post under 280 characters, including spaces, emojis, and line breaks.

    Requirements:
    - Make it x(twitter) post, not a blog post
    - Use emojis
    - Use line breaks
    - Use hashtags
    - Beutiful looking post
    """

    headers = {
        "Authorization": f"Bearer {GPT_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "provider-5/gpt-4o",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 150
    }
    try:
        response = requests.post(GPT_API_URL, headers=headers, json=data)
        response.raise_for_status()
        result = response.json()
        tweet_text = result["choices"][0]["message"]["content"].strip()
        # Replace literal '\n' with actual newline
        tweet_text = tweet_text.replace("\\n", "\n")
        # Remove empty lines and strip whitespace
        lines = [line.strip() for line in tweet_text.split("\n") if line.strip()]
        tweet_text = "\n".join(lines)
        # Ensure the tweet is under 280 characters
        if len(tweet_text) > 280:
            tweet_text = tweet_text[:277] + "..."
        print(f"Formatted tweet: {repr(tweet_text)}")
        return tweet_text
    except Exception as e:
        print(f"Error calling GPT-4o API: {e}")
        return None

def post_news():
    """Fetch news, format, and post to X."""
    posted_articles = load_posted_articles()
    news_items = fetch_rss_news()
    
    if not news_items:
        print("No news found for today, skipping post")
        return

    for item in news_items:
        article_id = item["link"] or item["title"]
        if article_id in posted_articles:
            continue

        tweet_text = format_tweet_with_gpt(item["title"], item["summary"])
        if not tweet_text:
            continue

        try:
            client.create_tweet(text=tweet_text)
            print(f"Successfully posted: {tweet_text}")
            save_posted_article(article_id)
            break
        except Exception as e:
            print(f"Error posting to X: {e}")

# Schedule posting every 30 minutes
schedule.every(30).minutes.do(post_news)

if __name__ == "__main__":
    print("Starting news posting bot...")
    post_news()
    while True:
        schedule.run_pending()
        time.sleep(60)