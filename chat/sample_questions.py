"""Dynamic sample questions that rotate daily and adapt to user activity."""
import hashlib
import random
from datetime import date

from django.db.models import Q

from .models import Message

# Broad pool — rotated daily so new chats feel fresh
QUESTION_POOL = [
    "What is Django and why use it?",
    "How do I learn Python step by step?",
    "Explain AI in simple terms",
    "Tell me a fun fact about space",
    "How does machine learning work?",
    "Write a short poem about technology",
    "What are the best practices for web security?",
    "Help me debug a Python error",
    "Explain REST APIs simply",
    "What is the difference between SQL and NoSQL?",
    "How do I build a portfolio website?",
    "Summarize climate change in 3 points",
    "What is blockchain in plain language?",
    "Give me tips for productive studying",
    "How does a neural network learn?",
    "What are microservices?",
    "Explain Git and version control",
    "Help me write a professional email",
    "What is cloud computing?",
    "How do I prepare for a tech interview?",
    "Explain photosynthesis simply",
    "What is Docker used for?",
    "Compare React vs Vue",
    "How does encryption keep data safe?",
    "What is agile development?",
    "Suggest a healthy daily routine",
    "Explain the water cycle to a child",
    "What is an API key?",
    "How do I manage time effectively?",
    "What causes inflation?",
    "Explain quantum computing basics",
    "How do I start a small business?",
    "What is renewable energy?",
    "Help me brainstorm app ideas",
    "Explain the solar system",
    "What is data science?",
    "How do vaccines work?",
    "What is UX design?",
    "Explain supply and demand",
    "How do I improve my English writing?",
]

TOPIC_KEYWORDS = {
    'python': ["How do I fix a Python IndentationError?", "Explain Python list comprehensions", "What are Python decorators?"],
    'django': ["How do Django models work?", "Explain Django middleware", "What is Django ORM?"],
    'javascript': ["Explain JavaScript closures", "What is the DOM?", "Compare var, let, and const"],
    'ai': ["How does ChatGPT work?", "What is fine-tuning in AI?", "Explain large language models"],
    'data': ["What is data cleaning?", "Explain pandas for beginners", "How do I visualize data?"],
    'business': ["How do I write a business plan?", "What is SWOT analysis?", "Explain marketing funnels"],
    'health': ["What are macronutrients?", "How much sleep do adults need?", "Explain mindfulness benefits"],
    'code': ["Help me understand recursion", "What is object-oriented programming?", "Explain Big O notation"],
    'file': ["How do I analyze a PDF with AI?", "Can you summarize my document?", "What can you do with uploaded images?"],
    'image': ["Describe what you see in my photo", "Help me edit photo ideas", "What objects are in this image?"],
}


def _daily_seed() -> int:
    return int(hashlib.md5(date.today().isoformat().encode()).hexdigest(), 16)


def _user_topic_hints(user) -> list[str]:
    """Infer topics from recent user messages and chat titles."""
    hints = set()
    recent = Message.objects.filter(
        session__user=user, is_ai=False
    ).order_by('-created_at')[:30]

    text_blob = ' '.join(m.content.lower() for m in recent)

    for keyword in TOPIC_KEYWORDS:
        if keyword in text_blob:
            hints.add(keyword)

    if any(w in text_blob for w in ('upload', 'pdf', 'document', 'file', 'docx', 'excel')):
        hints.add('file')
    if any(w in text_blob for w in ('photo', 'picture', 'image', 'jpg', 'png')):
        hints.add('image')

    return list(hints)


def get_sample_questions(user, count: int = 4) -> list[str]:
    """Return `count` sample questions — changes daily, influenced by user history."""
    rng = random.Random(_daily_seed() ^ hash(user.pk))

    pool = list(QUESTION_POOL)
    topics = _user_topic_hints(user)

    for topic in topics[:2]:
        extras = TOPIC_KEYWORDS.get(topic, [])
        if extras:
            pool.insert(rng.randint(0, len(pool)), rng.choice(extras))

    rng.shuffle(pool)
    selected = pool[:count]

    # Ensure unique short labels for buttons
    seen = set()
    unique = []
    for q in selected:
        key = q[:40]
        if key not in seen:
            seen.add(key)
            unique.append(q)

    while len(unique) < count:
        extra = rng.choice(QUESTION_POOL)
        if extra not in unique:
            unique.append(extra)

    return unique[:count]
