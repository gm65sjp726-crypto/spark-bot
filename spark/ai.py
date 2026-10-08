"""Генерация содержимого презентации через Claude (с запасным шаблоном без API-ключа)."""
import logging

import anthropic
from pydantic import BaseModel

from .config import ANTHROPIC_API_KEY, CLAUDE_MODEL

log = logging.getLogger(__name__)


class Slide(BaseModel):
    title: str
    bullets: list[str]
    notes: str


class Deck(BaseModel):
    title: str
    subtitle: str
    slides: list[Slide]
    conclusion: str


SYSTEM = (
    "Ты — профессиональный автор презентаций. Пишешь ёмко, конкретно и по делу: "
    "факты, цифры, примеры, без воды и общих фраз. Каждый пункт — законченная мысль "
    "до 120 символов. На каждом слайде 3–5 пунктов. В notes — текст для выступающего "
    "(2–4 предложения), который раскрывает слайд, а не повторяет его. "
    "Пиши на языке темы, если пользователь не попросил иначе."
)

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None


def generate_deck(topic: str, content_slides: int, wishes: str = "") -> dict:
    """Возвращает dict формата Deck. content_slides — число слайдов с содержанием
    (без титульного и финального)."""
    if _client is None:
        return _template_deck(topic, content_slides)

    prompt = (
        f"Тема презентации: {topic}\n"
        f"Нужно ровно {content_slides} содержательных слайдов (титульный и финальный не считаются, их не включай в slides).\n"
        "Выстрой логичную структуру: от контекста к сути и практическим выводам.\n"
        "conclusion — одна сильная итоговая фраза для финального слайда."
    )
    if wishes:
        prompt += f"\nПожелания пользователя: {wishes}"

    try:
        response = _client.messages.parse(
            model=CLAUDE_MODEL,
            max_tokens=16000,
            output_config={"effort": "low"},
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=Deck,
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            log.warning("Claude не вернул содержимое (stop_reason=%s)", response.stop_reason)
            return _template_deck(topic, content_slides)
        deck = response.parsed_output.model_dump()
        deck["slides"] = deck["slides"][:content_slides]
        return deck
    except anthropic.APIError:
        log.exception("Ошибка Claude API, используем шаблон")
        return _template_deck(topic, content_slides)


_SECTIONS = [
    ("Введение", ["Почему тема «{t}» важна сегодня", "Ключевые понятия и определения", "Цели этой презентации"]),
    ("Текущая ситуация", ["Как обстоят дела сейчас", "Основные участники и тренды", "Что изменилось за последние годы"]),
    ("Проблема", ["Главные сложности в области «{t}»", "Чем они опасны и кого затрагивают", "Почему старые подходы не работают"]),
    ("Решение", ["Предлагаемый подход", "Как он устраняет проблему", "Необходимые ресурсы"]),
    ("Преимущества", ["Экономия времени и средств", "Рост качества и результатов", "Масштабируемость"]),
    ("Примеры", ["Успешные кейсы применения", "Цифры и результаты", "Чему они нас учат"]),
    ("Риски", ["Возможные препятствия", "Как их снизить", "План Б"]),
    ("План действий", ["Шаг 1: подготовка", "Шаг 2: внедрение", "Шаг 3: оценка результатов"]),
    ("Перспективы", ["Как будет развиваться «{t}»", "Новые возможности", "Что делать уже сейчас"]),
]


def _template_deck(topic: str, n: int) -> dict:
    slides = []
    for i in range(n):
        title, bullets = _SECTIONS[i % len(_SECTIONS)]
        slides.append({
            "title": title,
            "bullets": [b.format(t=topic) for b in bullets],
            "notes": f"Раздел «{title}» по теме «{topic}».",
        })
    return {
        "title": topic,
        "subtitle": "Презентация",
        "slides": slides,
        "conclusion": f"{topic}: время действовать",
    }
