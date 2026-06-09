from dataclasses import dataclass


@dataclass
class Contact:
    """Represents a decision-maker found via Prospeo."""
    name: str
    title: str
    linkedin_url: str
    company_name: str
    company_domain: str

    @property
    def first_name(self) -> str:
        """Extract first name from full name."""
        return self.name.split()[0] if self.name else ""

    def __hash__(self) -> int:
        return hash(self.linkedin_url.lower())

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Contact):
            return NotImplemented
        return self.linkedin_url.lower() == other.linkedin_url.lower()
