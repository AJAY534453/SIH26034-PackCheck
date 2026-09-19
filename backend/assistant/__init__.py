"""In-application assistant package.

Public surface: :func:`backend.services.assistant_service.respond` — grounded answers about this
application, its workflows and the legal references it actually holds.
"""
from backend.assistant.knowledge import GLOSSARY, SUGGESTED_QUESTIONS, TOPICS, Topic, topic_by_id

__all__ = ["GLOSSARY", "SUGGESTED_QUESTIONS", "TOPICS", "Topic", "topic_by_id"]
