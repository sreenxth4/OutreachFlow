from dataclasses import dataclass


@dataclass
class Company:
    """Represents a company found via Ocean.io lookalike search."""
    name: str
    domain: str

    def __hash__(self) -> int:
        return hash(self.domain.lower())

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Company):
            return NotImplemented
        return self.domain.lower() == other.domain.lower()
