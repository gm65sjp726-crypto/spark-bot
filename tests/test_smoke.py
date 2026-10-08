import os
import tempfile

os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["DB_PATH"] = ":memory:"

from pptx import Presentation  # noqa: E402

from spark import db  # noqa: E402
from spark.ai import generate_deck  # noqa: E402
from spark.pptx_builder import THEMES, build_pptx  # noqa: E402


def test_every_theme_builds():
    deck = generate_deck("Возобновляемая энергетика", 8)
    with tempfile.TemporaryDirectory() as tmp:
        for key in THEMES:
            path = build_pptx(deck, key, os.path.join(tmp, f"{key}.pptx"))
            assert len(Presentation(path).slides) == 10


def test_sparks_spend_and_monthly_floor():
    db.get_user(1)
    assert db.try_spend(1, 100)
    assert not db.try_spend(1, 10_000)
    assert db.get_user(1)["sparks"] == 300
