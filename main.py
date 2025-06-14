import tweepy
from dotenv import load_dotenv
import os

# Load environment variables from .env file
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

def post():
    tweet_text = """🗞️ Crypto News: BlackRock just bought $1,000,000 worth of $SOL! 🚨\n📈 Institutional eyes now on #Solana as adoption grows.\n💬 Could this spark an altcoin rally?\n🔁 RT if you're bullish on $SOL\n#CryptoNews #BlackRock #Web3 #CryptoBot"""

    if len(tweet_text) > 280:
        print("Tweet is too long")
        return

    try:
        client.create_tweet(text=tweet_text)
        print("Successfully posted to X")
    except Exception as e:
        print(f"Error posting to X: {e}")

if __name__ == "__main__":
    post()