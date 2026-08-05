"""Domain scopes for organizer authorization.

A scope names an area of an organization's data, so a membership can eventually grant access to
some areas and not others: a program committee reads talks and videos without seeing sponsor
finances, comms reaches the mailing list without touching the CFP.

Today every organizer membership grants every scope. The scope argument exists now anyway, because
threading it through call sites later is the expensive part and passing an always-satisfied scope
costs nothing.

Adding a member here needs no migration. `OrganizationMembership.scopes` is a JSONField, and
memberships that have not been restricted grant whatever this enum contains.

A plain StrEnum rather than `models.TextChoices`, matching `integrations.providers.base.Capability`.
"""

from enum import StrEnum


class Scope(StrEnum):
    PROGRAM = "program"
    VIDEOS = "videos"
    SPONSORSHIP = "sponsorship"
    COMMS = "comms"

    @property
    def label(self) -> str:
        return _SCOPE_LABELS[self]

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        return [(member.value, member.label) for member in cls]

    @classmethod
    def values(cls) -> set[str]:
        return {member.value for member in cls}


_SCOPE_LABELS = {
    Scope.PROGRAM: "Program (CFP, talks, speakers)",
    Scope.VIDEOS: "Videos and captions",
    Scope.SPONSORSHIP: "Sponsors and donations",
    Scope.COMMS: "Mailing list and announcements",
}
