from dataclasses import dataclass
from .contact import Contact


@dataclass
class Lead:
    """A contact with a verified email address, ready for outreach."""
    contact: Contact
    email: str

    def __hash__(self) -> int:
        return hash(self.email.lower())

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Lead):
            return NotImplemented
        return self.email.lower() == other.email.lower()
