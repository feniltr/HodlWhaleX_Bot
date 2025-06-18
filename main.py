import tweepy
import feedparser
import requests
import schedule
import time
import logging
import sys
import os
import traceback
import json
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from typing import Set, List, Dict, Optional
from telegram import Bot


load_dotenv()

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
        self.retry_delay = 30
        self.gpt_calls = {'minute': 0, 'day': 0, 'last_reset': datetime.now(timezone.utc)}
        self.x_calls = {'day': 0, 'last_reset': datetime.now(timezone.utc).date()}
        
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
        """Validate required environment variables."""
        required_vars = [
            "API_KEY", "API_SECRET", "ACCESS_TOKEN", 
            "ACCESS_TOKEN_SECRET", "BEARER_TOKEN", "GPT_API_KEY",
            "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"
        ]
        missing_vars = [var for var in required_vars if not os.getenv(var)]
        if missing_vars:
            logger.error(f"Missing environment variables: {missing_vars}")
            sys.exit(1)

    def setup_api_client(self):
        """Initialize X API client."""
        try:
            self.client = tweepy.Client(
                bearer_token=os.getenv("BEARER_TOKEN"),
                consumer_key=os.getenv("API_KEY"),
                consumer_secret=os.getenv("API_SECRET"),
                access_token=os.getenv("ACCESS_TOKEN"),
                access_token_secret=os.getenv("ACCESS_TOKEN_SECRET"),
                wait_on_rate_limit=False
            )
        except Exception as e:
            logger.error(f"Failed to initialize X API client: {e}")
            self.send_telegram_notification(f"X API initialization failed: {e}")
            sys.exit(1)

    def load_posted_articles(self) -> Set[str]:
        """Load previously posted articles, remove entries older than 30 days."""
        if not os.path.exists(self.posted_articles_file):
            return set()
        
        cutoff_date = datetime.now(timezone.utc).date() - timedelta(days=30)
        kept_articles = set()
        
        try:
            with open(self.posted_articles_file, "r", encoding='utf-8') as f:
                lines = f.readlines()
            with open(self.posted_articles_file, "w", encoding='utf-8') as f:
                for line in lines:
                    if ":" in line:
                        date_str, article_id = line.strip().split(":", 1)
                        try:
                            post_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                            if post_date >= cutoff_date:
                                kept_articles.add(article_id)
                                f.write(line)
                        except ValueError:
                            continue
            return kept_articles
        except Exception as e:
            logger.error(f"Error loading posted articles: {e}")
            self.send_telegram_notification(f"Error loading posted articles: {e}")
            return set()

    def save_posted_article(self, article_id: str):
        """Save posted article with current date."""
        try:
            with open(self.posted_articles_file, "a", encoding='utf-8') as f:
                f.write(f"{datetime.now(timezone.utc).date()}:{article_id}\n")
        except Exception as e:
            logger.error(f"Error saving posted article: {e}")
            self.send_telegram_notification(f"Error saving posted article: {e}")

    def check_api_limits(self, api_type: str) -> bool:
        """Check and update API call limits."""
        now = datetime.now(timezone.utc)
        
        if api_type == "gpt":
            if (now - self.gpt_calls['last_reset']).total_seconds() >= 60:
                self.gpt_calls['minute'] = 0
                self.gpt_calls['last_reset'] = now
            if now.date() != self.gpt_calls['last_reset'].date():
                self.gpt_calls['day'] = 0
            if self.gpt_calls['minute'] >= 5 or self.gpt_calls['day'] >= 300:
                logger.warning("GPT API limit reached")
                self.send_telegram_notification("GPT API limit reached")
                return False
            return True

        elif api_type == "x":
            if now.date() != self.x_calls['last_reset']:
                self.x_calls['day'] = 0
                self.x_calls['last_reset'] = now.date()
            if self.x_calls['day'] >= 16:
                logger.warning("X API limit reached")
                self.send_telegram_notification("X API limit reached")
                return False
            self.x_calls['day'] += 1
            return True

        return False

    def fetch_rss_news(self) -> List[Dict]:
        """Fetch and filter news from RSS feeds for today."""
        today = datetime.now(timezone.utc).date()
        news_items = []
        
        for feed_url in self.rss_feeds:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries:
                    pub_date_struct = entry.get("published_parsed", entry.get("updated_parsed"))
                    if not pub_date_struct:
                        continue
                    pub_date = datetime(*pub_date_struct[:6], tzinfo=timezone.utc)
                    if pub_date.date() != today:
                        continue
                    news_items.append({
                        "title": entry.get("title", ""),
                        "link": entry.get("link", ""),
                        "summary": entry.get("summary", entry.get("description", "")),
                        "pub_date": pub_date
                    })
            except Exception as e:
                logger.error(f"Error fetching RSS feed {feed_url}: {e}")
                self.send_telegram_notification(f"RSS feed error: {e}")
        
        news_items.sort(key=lambda x: x["pub_date"])
        return news_items

    def is_crypto_news(self, title: str, summary: str) -> Dict:
        """Use GPT-4o to determine if news is crypto-related and not promotional."""
        if not self.check_api_limits("gpt"):
            return {"news_to_post": "", "status": False}
        
        # Ensure title and summary are strings and escape curly braces
        title = str(title).replace("{", "{{").replace("}", "}}") if title else ""
        summary = str(summary).replace("{", "{{").replace("}", "}}") if summary else ""
        
        # Debug logging to inspect inputs
        logger.debug(f"Processing title: {title}")
        logger.debug(f"Processing summary: {summary}")
        
        try:
            prompt = f"""
Title: {title}
Summary: {summary}

Analyze if this news is:
1. Related to cryptocurrency, blockchain, or Web3
2. Not promotional (e.g., not about stacking coins in wallets or promoting services)
3. Complete (not redirecting to another page for full content)
4. Crypto whale activity
5. Crypto news price changes 

Return JSON:
{{
    "news_to_post": "Formatted tweet text under 280 characters with emojis, line breaks, and hashtags, and relevant hashtags, and relevant emojis, and relevant line breaks, and relevant formatting, Eye catching title, Dont use \"\" in news_to_post or anything which is json unfriendly ",
    "status": true/false
}}
Status is true only if all criteria are met.
"""
        except ValueError as ve:
            logger.error(f"F-string error in prompt construction: {ve}")
            self.send_telegram_notification(f"F-string error in is_crypto_news: {ve}")
            return {"news_to_post": "", "status": False}

        headers = {
            "Authorization": f"Bearer {os.getenv('GPT_API_KEY')}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "provider-5/gpt-4o",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 200
        }
        
        for attempt in range(self.max_retries):
            try:
                response = requests.post(self.gpt_api_url, headers=headers, json=data, timeout=15)
                logger.debug(f"GPT API response status: {response.status_code}, content: {response.text[:100]}")
                response.raise_for_status()
                
                # Only increment GPT call counter on successful request
                self.gpt_calls['minute'] += 1
                self.gpt_calls['day'] += 1
                
                try:
                    result = response.json()
                except ValueError as ve:
                    logger.error(f"Failed to parse GPT API response: {ve}, response: {response.text[:200]}")
                    self.send_telegram_notification(f"Failed to parse GPT API response: {ve}, response: {response.text[:200]}")
                    return {"news_to_post": "", "status": False}
                
                output = result["choices"][0]["message"]["content"].strip()
                logger.debug(f"GPT output before JSON parsing: {output}")
                
                # Strip markdown code block markers if present
                if output.startswith("```json"):
                    output = output[7:]  # Remove ```json
                if output.startswith("```"):
                    output = output[3:]   # Remove ``` if no language specified
                if output.endswith("```"):
                    output = output[:-3]  # Remove closing ```
                output = output.strip()
                
                try:
                    parsed = json.loads(output)
                except json.JSONDecodeError as je:
                    logger.error(f"Failed to parse GPT output as JSON: {je}, output: {output[:200]}")
                    self.send_telegram_notification(f"Failed to parse GPT output as JSON: {je}, output: {output[:200]}")
                    return {"news_to_post": "", "status": False}
                
                if parsed.get("status", False):
                    tweet_text = parsed.get("news_to_post", "").replace("\\n", "\n")
                    if len(tweet_text) > 280:
                        tweet_text = tweet_text[:277] + "..."
                    parsed["news_to_post"] = tweet_text
                return parsed
            except requests.exceptions.HTTPError as he:
                logger.error(f"GPT API HTTP error: {he}, status: {response.status_code}, response: {response.text[:200]}")
                self.send_telegram_notification(f"GPT API HTTP error: {he}, status: {response.status_code}")
                if response.status_code == 429:  # Rate limit
                    logger.warning("GPT API rate limit reached. Waiting 2 minutes...")
                    self.send_telegram_notification("GPT API rate limit reached. Waiting 2 minutes...")
                    time.sleep(120)  # Wait 2 minutes
                    if attempt < self.max_retries - 1:
                        continue
                    else:
                        logger.error("GPT API rate limit persists after retries. Closing program.")
                        self.send_telegram_notification("GPT API rate limit persists after retries. Closing program.")
                        sys.exit(1)
                return {"news_to_post": "", "status": False}
            except Exception as e:
                logger.error(f"GPT API error: {e}, attempt {attempt + 1}")
                self.send_telegram_notification(f"GPT API error: {e}, attempt {attempt + 1}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
                else:
                    return {"news_to_post": "", "status": False}
        
        # Fallback return if all retry attempts are exhausted
        return {"news_to_post": "", "status": False}

    def send_telegram_notification(self, message: str):
        """Send Telegram notification."""
        try:
            self.telegram_bot.send_message(chat_id=self.telegram_chat_id, text=message)
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")

    def post_news(self):
        """Fetch, filter, and post news to X."""
        try:
            posted_articles = self.load_posted_articles()
            news_items = self.fetch_rss_news()
            
            if not news_items:
                logger.info("No news found for today")
                return

            for item in news_items:
                article_id = item["link"] or item["title"]
                if article_id in posted_articles:
                    continue

                result = self.is_crypto_news(item["title"], item["summary"])
                if not result["status"]:
                    logger.info(f"Skipping non-crypto/promotional news: {item['title']}")
                    self.save_posted_article(article_id)
                    continue

                tweet_text = result["news_to_post"]
                if len(tweet_text) > 280:
                    logger.warning(f"Tweet too long ({len(tweet_text)} chars): {item['title']}")
                    self.send_telegram_notification(
                        f"Tweet too long ({len(tweet_text)} chars) for: {item['title'][:50]}..."
                    )
                    self.save_posted_article(article_id)
                    continue

                if not self.check_api_limits("x"):
                    return

                for attempt in range(self.max_retries):
                    try:
                        self.client.create_tweet(text=tweet_text)
                        logger.info(f"Posted: {tweet_text}")
                        self.save_posted_article(article_id)
                        
                        next_post_time_utc = datetime.now(timezone.utc) + timedelta(minutes=1.5)
                        next_post_time_ist = next_post_time_utc + timedelta(hours=5, minutes=30)
                        logger.debug(f"Next post time UTC: {next_post_time_utc}, IST: {next_post_time_ist}")
                        try:
                            self.send_telegram_notification(
                                f"Posted on X\nNext post: {next_post_time_ist.strftime('%Y-%m-%d %H:%M:%S IST')}"
                            )
                        except ValueError as ve:
                            logger.error(f"strftime error: {ve}")
                            self.send_telegram_notification(f"strftime error in post_news: {ve}")
                        return
                    except tweepy.TooManyRequests as e:
                        logger.warning(f"X API rate limit: {e}")
                        self.send_telegram_notification(f"X API rate limit: {e}")
                        time.sleep(300)
                    except Exception as e:
                        logger.error(f"X posting error: {e}")
                        self.send_telegram_notification(f"X posting error: {e}")
                        if attempt < self.max_retries - 1:
                            time.sleep(self.retry_delay)
        except Exception as e:
            logger.error(f"Post_news error: {e}\n{traceback.format_exc()}")
            self.send_telegram_notification(f"Post_news error: {e}\n{traceback.format_exc()}")

    def run(self):
        """Main execution loop."""
        print("HodlWhaleX bot starting...")
        self.send_telegram_notification("HodlWhaleX started...")
        
        # Run the first post immediately
        print("Running initial post...")
        self.post_news()
        
        # Schedule subsequent posts every 90 minutes
        schedule.every(90).minutes.do(self.post_news)
        print("Bot is now running. Next scheduled post in 90 minutes...")
        
        while True:
            try:
                schedule.run_pending()
                time.sleep(1)
            except KeyboardInterrupt:
                print("Bot stopped by user")
                self.send_telegram_notification("HodlWhaleX stopped...")
                break
            except Exception as e:
                logger.error(f"Main loop error: {e}\n{traceback.format_exc()}")
                self.send_telegram_notification(f"Main loop error: {e}\n{traceback.format_exc()}")
                time.sleep(60)

if __name__ == "__main__":
    bot = XAutopostingBot()
    bot.run()