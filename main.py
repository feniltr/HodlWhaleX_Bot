import tweepy
import feedparser
import requests
import schedule
import time
import logging
import sys
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import os
from typing import Set, List, Dict, Optional
from telegram import Bot

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('x_bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Custom logging handler for Telegram notifications
class TelegramHandler(logging.Handler):
    def __init__(self, bot, chat_id):
        super().__init__()
        self.bot = bot
        self.chat_id = chat_id

    def emit(self, record):
        log_entry = self.format(record)
        try:
            self.bot.send_message(chat_id=self.chat_id, text=log_entry)
        except Exception as e:
            logging.getLogger().error(f"Failed to send Telegram notification: {e}")

class XAutopostingBot:
    def __init__(self):
        self.validate_environment()
        self.setup_api_client()
        self.rss_feeds = [
            "https://www.coindesk.com/arc/outboundfeeds/rss",
            "https://cointelegraph.com/rss",
            "https://decrypt.co/feed"
        ]
        self.posted_articles_file = "posted_articles.txt"
        self.gpt_api_url = "https://api.a4f.co/v1/chat/completions"
        self.max_retries = 3
        self.retry_delay = 60  # seconds
        
        # Telegram setup
        self.telegram_token = os.getenv("TELEGRAM_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.telegram_bot = Bot(token=self.telegram_token)
        
        # Set up Telegram handler for warnings and errors
        telegram_handler = TelegramHandler(self.telegram_bot, self.telegram_chat_id)
        telegram_handler.setLevel(logging.WARNING)
        telegram_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(telegram_handler)

    def validate_environment(self):
        """Validate that all required environment variables are set."""
        required_vars = [
            "API_KEY", "API_SECRET", "ACCESS_TOKEN", 
            "ACCESS_TOKEN_SECRET", "BEARER_TOKEN", "GPT_API_KEY",
            "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"
        ]
        missing_vars = [var for var in required_vars if not os.getenv(var)]
        if missing_vars:
            logger.error(f"Missing required environment variables: {missing_vars}")
            sys.exit(1)
        logger.info("All required environment variables found")

    def setup_api_client(self):
        """Initialize X API client with error handling."""
        try:
            self.client = tweepy.Client(
                bearer_token=os.getenv("BEARER_TOKEN"),
                consumer_key=os.getenv("API_KEY"),
                consumer_secret=os.getenv("API_SECRET"),
                access_token=os.getenv("ACCESS_TOKEN"),
                access_token_secret=os.getenv("ACCESS_TOKEN_SECRET"),
                wait_on_rate_limit=True
            )
            logger.info("X API client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize X API client: {e}")
            sys.exit(1)

    def load_posted_articles(self) -> Set[str]:
        """Load previously posted article titles/URLs from file, remove entries older than 30 days."""
        if not os.path.exists(self.posted_articles_file):
            logger.info("No posted articles file found, starting fresh")
            return set()
        
        cutoff_date = datetime.now(timezone.utc).date() - timedelta(days=30)
        kept_articles = set()
        new_lines = []
        
        try:
            with open(self.posted_articles_file, "r", encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    if ":" in line:
                        date_str, article_id = line.split(":", 1)
                        try:
                            post_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                            if post_date < cutoff_date:
                                logger.debug(f"Removing old entry: {line}")
                                continue
                            kept_articles.add(article_id)
                            new_lines.append(line + "\n")
                        except ValueError:
                            logger.warning(f"Malformed date in entry, treating as old: {line}")
                            continue
                    else:
                        logger.warning(f"Old format entry, removing: {line}")
                        continue
            
            with open(self.posted_articles_file, "w", encoding='utf-8') as f:
                f.writelines(new_lines)
            
            logger.info(f"Loaded {len(kept_articles)} previously posted articles")
            return kept_articles
        except Exception as e:
            logger.error(f"Error loading posted articles: {e}")
            return set()

    def save_posted_article(self, article_id: str):
        """Save posted article title/URL to file with the current date."""
        post_date = datetime.now(timezone.utc).date()
        try:
            with open(self.posted_articles_file, "a", encoding='utf-8') as f:
                f.write(f"{post_date}:{article_id}\n")
            logger.debug(f"Saved posted article: {article_id}")
        except Exception as e:
            logger.error(f"Error saving posted article: {e}")

    def fetch_rss_news(self) -> List[Dict]:
        """Fetch news from RSS feeds, filter by current date, and sort by pubDate (oldest first)."""
        today = datetime.now(timezone.utc).date()
        news_items = []
        
        for feed_url in self.rss_feeds:
            try:
                logger.debug(f"Fetching RSS feed: {feed_url}")
                feed = feedparser.parse(feed_url)
                logger.info(f"Fetched {len(feed.entries)} articles from {feed_url}")
                
                for entry in feed.entries:
                    pub_date_struct = entry.get("published_parsed", entry.get("updated_parsed", None))
                    if not pub_date_struct:
                        logger.debug(f"No pubDate for article: {entry.get('title', 'No title')}")
                        continue
                    pub_date = datetime(*pub_date_struct[:6], tzinfo=timezone.utc)
                    if pub_date.date() != today:
                        logger.debug(f"Article not from today: {entry.get('title', 'No title')} (Date: {pub_date.date()})")
                        continue
                    
                    logger.info(f"Found article from today: {entry.get('title', 'No title')}")
                    news_items.append({
                        "title": entry.get("title", "No title"),
                        "link": entry.get("link", ""),
                        "summary": entry.get("summary", entry.get("description", "")),
                        "pub_date": pub_date
                    })
            except Exception as e:
                logger.error(f"Error fetching RSS feed {feed_url}: {e}")
        
        news_items.sort(key=lambda x: x["pub_date"], reverse=False)
        logger.info(f"Found {len(news_items)} news items from today")
        return news_items

    def format_tweet_with_gpt(self, news_title: str, news_summary: str) -> Optional[str]:
        """Use GPT-4o API to summarize and format news into a tweet."""
        prompt = f"""
        {news_title} - {news_summary}

        Summarize this news into a single X post under 280 characters, including spaces, emojis, and line breaks.

        Requirements:
        - Make it x(twitter) post, not a blog post
        - Use emojis
        - Use line breaks
        - Use hashtags
        - Beautiful looking post
        """

        headers = {
            "Authorization": f"Bearer {os.getenv('GPT_API_KEY')}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "provider-5/gpt-4o",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 150
        }
        
        for attempt in range(self.max_retries):
            try:
                response = requests.post(self.gpt_api_url, headers=headers, json=data, timeout=30)
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
                
                logger.debug(f"Formatted tweet: {repr(tweet_text)}")
                return tweet_text
                
            except requests.exceptions.Timeout:
                logger.warning(f"GPT API timeout on attempt {attempt + 1}")
            except requests.exceptions.RequestException as e:
                logger.warning(f"GPT API request error on attempt {attempt + 1}: {e}")
            except Exception as e:
                logger.error(f"Unexpected error calling GPT-4o API on attempt {attempt + 1}: {e}")
            
            if attempt < self.max_retries - 1:
                time.sleep(self.retry_delay)
        
        logger.error("Failed to format tweet after all retries")
        return None

    def send_telegram_notification(self, message: str):
        """Send a custom notification to Telegram."""
        try:
            self.telegram_bot.send_message(chat_id=self.telegram_chat_id, text=message)
            logger.info(f"Telegram notification sent: {message}")
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")

    def post_news(self):
        """Fetch news, format, and post to X."""
        try:
            logger.info("Starting news posting process")
            posted_articles = self.load_posted_articles()
            news_items = self.fetch_rss_news()
            
            if not news_items:
                logger.info("No news found for today, skipping post")
                return

            for item in news_items:
                article_id = item["link"] or item["title"]
                if article_id in posted_articles:
                    logger.debug(f"Article already posted: {item['title']}")
                    continue

                tweet_text = self.format_tweet_with_gpt(item["title"], item["summary"])
                if not tweet_text:
                    logger.warning(f"Failed to format tweet for: {item['title']}")
                    continue

                for attempt in range(self.max_retries):
                    try:
                        self.client.create_tweet(text=tweet_text)
                        logger.info(f"Successfully posted tweet: {tweet_text}")
                        self.save_posted_article(article_id)
                        
                        # Calculate next post time in UTC and IST
                        next_post_time_utc = datetime.now(timezone.utc) + timedelta(minutes=30)
                        next_post_time_ist = next_post_time_utc + timedelta(hours=5, minutes=30)
                        next_post_str_utc = next_post_time_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
                        next_post_str_ist = next_post_time_ist.strftime("%Y-%m-%d %H:%M:%S IST")
                        
                        # Send updated Telegram notification
                        self.send_telegram_notification(
                            f"Successfully posted on X\nNext post scheduled at:\nIST: {next_post_str_ist}\nUTC: {next_post_str_utc}"
                        )
                        return  # Successfully posted, exit function
                    except tweepy.TooManyRequests:
                        logger.warning("Rate limit exceeded, waiting...")
                        time.sleep(900)  # Wait 15 minutes
                    except tweepy.Forbidden as e:
                        logger.error(f"Forbidden error posting to X: {e}")
                        break  # Don't retry on permission errors
                    except Exception as e:
                        logger.warning(f"Error posting to X on attempt {attempt + 1}: {e}")
                        if attempt < self.max_retries - 1:
                            time.sleep(self.retry_delay)
                
                logger.error(f"Failed to post tweet after all retries: {item['title']}")
                
        except Exception as e:
            logger.error(f"Unexpected error in post_news: {e}")

    def run(self):
        """Main execution loop."""
        logger.info("Starting X autoposting bot...")
        self.send_telegram_notification("HodlWhaleX started...")
        
        # Schedule posting every 30 minutes
        schedule.every(30).minutes.do(self.post_news)
        
        # Run once immediately
        self.post_news()
        
        while True:
            try:
                schedule.run_pending()
                time.sleep(60)
            except KeyboardInterrupt:
                self.send_telegram_notification("HodlWhaleX stopped...")
                logger.info("Bot stopped by user")
                break
            except Exception as e:
                logger.error(f"Unexpected error in main loop: {e}")
                time.sleep(300)  # Wait 5 minutes before continuing

if __name__ == "__main__":
    bot = XAutopostingBot()
    bot.run()