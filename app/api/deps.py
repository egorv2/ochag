from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.config import Settings, get_settings
from app.core.confirmations import ConfirmationService
from app.core.orchestrator import Orchestrator
from app.integrations.contacts import ContactBook
from app.integrations.dispatcher import MessageDispatcher
from app.llm import build_llm_chain
from app.search.providers import build_search_provider
from app.services.audio_client import AudioClient


@dataclass
class Services:
    settings: Settings
    llm: object
    orchestrator: Orchestrator
    confirmations: ConfirmationService
    dispatcher: MessageDispatcher
    contacts: ContactBook
    audio: AudioClient


@lru_cache
def get_services() -> Services:
    settings = get_settings()
    llm = build_llm_chain(settings)
    contacts = ContactBook(settings)
    search = build_search_provider(settings)
    dispatcher = MessageDispatcher(settings)
    confirmations = ConfirmationService(settings, dispatcher)
    orchestrator = Orchestrator(settings, llm, contacts, search, confirmations)
    return Services(
        settings=settings,
        llm=llm,
        orchestrator=orchestrator,
        confirmations=confirmations,
        dispatcher=dispatcher,
        contacts=contacts,
        audio=AudioClient(settings),
    )
