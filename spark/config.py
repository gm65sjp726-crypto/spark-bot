import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
DB_PATH = os.getenv("DB_PATH", "bot.db")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}

# ====== Экономика ======
MONTHLY_SPARKS = int(os.getenv("MONTHLY_SPARKS", "400"))
SPARKS_PER_SLIDE = int(os.getenv("SPARKS_PER_SLIDE", "10"))
RESTYLE_COST = int(os.getenv("RESTYLE_COST", "0"))
REFERRAL_BONUS = int(os.getenv("REFERRAL_BONUS", "100"))

SLIDE_OPTIONS = [5, 7, 10, 12, 15]
MAX_TOPIC_LEN = 300
MAX_WISHES_LEN = 500


def presentation_cost(slides: int) -> int:
    return slides * SPARKS_PER_SLIDE
