"""Configuration management for OutreachFlow.

Loads all settings from .env file using python-dotenv.
All API keys and limits are managed through the Settings dataclass.
"""

import os
import sys
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass
class Settings:
    """Pipeline configuration loaded from environment variables."""
    # API keys
    ocean_api_key: str
    prospeo_api_key: str
    brevo_api_key: str

    # Brevo sender config
    brevo_sender_email: str = "sreenath@outreachflow.me"
    brevo_sender_name: str = "Sreenath"

    # Pipeline limits
    max_companies: int = 5
    max_contacts_per_company: int = 2
    max_emails_to_send: int = 10


def load_settings() -> Settings:
    """Load settings from .env file.
    
    Validates that all required API keys are present.
    Exits with clear error message if any are missing.
    """
    # Load .env from project root
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    load_dotenv(env_path)

    required_keys = [
        "OCEAN_API_KEY",
        "PROSPEO_API_KEY",
        "BREVO_API_KEY",
    ]

    missing = [key for key in required_keys if not os.getenv(key)]
    if missing:
        print(f"\n❌ Missing required environment variables: {', '.join(missing)}")
        print("   Copy .env.example to .env and fill in your API keys.")
        sys.exit(1)

    return Settings(
        ocean_api_key=os.getenv("OCEAN_API_KEY", ""),
        prospeo_api_key=os.getenv("PROSPEO_API_KEY", ""),
        brevo_api_key=os.getenv("BREVO_API_KEY", ""),
        brevo_sender_email=os.getenv("BREVO_SENDER_EMAIL", "sreenath@outreachflow.me"),
        brevo_sender_name=os.getenv("BREVO_SENDER_NAME", "Sreenath"),
        max_companies=int(os.getenv("MAX_COMPANIES", "5")),
        max_contacts_per_company=int(os.getenv("MAX_CONTACTS_PER_COMPANY", "2")),
        max_emails_to_send=int(os.getenv("MAX_EMAILS_TO_SEND", "10")),
    )
