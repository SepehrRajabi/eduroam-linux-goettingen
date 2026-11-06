"""Institution profile: what the installer configures, and for whom."""

from dataclasses import dataclass
from enum import StrEnum


class EapMethod(StrEnum):
    """Outer EAP methods understood by the installer."""

    PEAP = "PEAP"
    TTLS = "TTLS"
    TLS = "TLS"
    PWD = "PWD"


#: Outer methods that authenticate with a username and password.
PASSWORD_METHODS = frozenset({EapMethod.PEAP, EapMethod.TTLS})


@dataclass(frozen=True, slots=True, kw_only=True)
class Profile:
    """Connection settings for one institution.

    Attributes:
        institution: Institution name shown to the user.
        profile_name: User group the profile is meant for.
        url: Support web page.
        email: Support email address.
        title: Title of dialog windows.
        init_info: Welcome text with ``{0}`` institution, ``{1}`` email, ``{2}`` URL.
        init_confirmation: Membership question, ``{0}`` institution, ``{1}`` group.
        terms_of_use: Terms the user must accept; skipped when empty.
        eap_outer: Outer EAP method.
        eap_inner: Inner (phase 2) method; ``SILVERBULLET`` selects an embedded
            client certificate.
        ssids: Wireless networks to configure.
        del_ssids: Wireless networks whose saved connections are removed.
        servers: RADIUS server names in ``DNS:`` form, for ``altsubject_match``.
        servers_cn: RADIUS server domain, for domain matching.
        server_match: RADIUS server subject, for NetworkManager before 0.9.
        anonymous_identity: Outer identity; the username is used when empty.
        user_realm: Realm expected in usernames.
        verify_user_realm_input: Reject usernames outside ``user_realm``.
        hint_user_input: Require the exact realm instead of a realm suffix, and
            suggest it when asking for the username.
        use_other_tls_id: Do not read the username from the client certificate.
        offer_wired: Offer to configure 802.1X on wired interfaces too.
        ca_certificate: Trusted CA certificates, PEM encoded.
        sb_user_file: Base64 encoded client certificate for ``SILVERBULLET``.
    """

    institution: str
    profile_name: str
    url: str
    email: str
    title: str = "eduroam CAT"
    init_info: str
    init_confirmation: str
    terms_of_use: str = ""
    eap_outer: EapMethod
    eap_inner: str
    ssids: tuple[str, ...]
    del_ssids: tuple[str, ...] = ()
    servers: tuple[str, ...]
    servers_cn: str
    server_match: str
    anonymous_identity: str = ""
    user_realm: str = ""
    verify_user_realm_input: bool = False
    hint_user_input: bool = False
    use_other_tls_id: bool = False
    offer_wired: bool = False
    ca_certificate: str
    sb_user_file: str = ""

    @property
    def welcome_text(self) -> str:
        """Return the formatted welcome message."""
        return self.init_info.format(self.institution, self.email, self.url)

    @property
    def confirmation_text(self) -> str:
        """Return the formatted membership question."""
        return self.init_confirmation.format(self.institution, self.profile_name)

    def outer_identity(self, username: str) -> str:
        """Return the identity sent outside the encrypted tunnel."""
        return self.anonymous_identity or username


GOETTINGEN = Profile(
    institution="University Goettingen",
    profile_name="University of Göttingen",
    url="https://wiki.student.uni-goettingen.de/support/wlan",
    email="campussupport@uni-goettingen.de",
    init_info=(
        "This installer has been prepared for {0}\n\nMore information and comments:"
        "\n\nEMAIL: {1}\nWWW: {2}\n\nInstaller created with software from the "
        "GEANT project."
    ),
    init_confirmation=(
        "This installer will only work properly if you are a member of {0} and "
        "the user group: {1}."
    ),
    eap_outer=EapMethod.PEAP,
    eap_inner="MSCHAPV2",
    ssids=("eduroam",),
    servers=("DNS:eduroam.gwdg.de",),
    servers_cn="eduroam.gwdg.de",
    server_match="eduroam.gwdg.de",
    anonymous_identity="wifi@uni-goettingen.de",
    user_realm="uni-goettingen.de",
    ca_certificate="""\
-----BEGIN CERTIFICATE-----
MIICrTCCAjKgAwIBAgIUbbmDONSWxsxqQXFtKTuEf3FXKLYwCgYIKoZIzj0EAwIw
NjELMAkGA1UEBhMCREUxDTALBgNVBAoMBEdXREcxGDAWBgNVBAMMD0dXREcgZWR1
cm9hbSBDQTAgFw0yNjA2MTkwOTM2MTBaGA8yMDU2MDYxMTA5MzYxMFowNjELMAkG
A1UEBhMCREUxDTALBgNVBAoMBEdXREcxGDAWBgNVBAMMD0dXREcgZWR1cm9hbSBD
QTB2MBAGByqGSM49AgEGBSuBBAAiA2IABB58cJCN291UeAyafWasiLvNV4tg5Ld3
/OEG1rCs5Gs/bIp4/DW21mhbIhRtDizSjopPj+U0fDKSW6lNPGyVhzW6qjWiCd8T
OPnW3n74UvcrqXUGt+gTxcz4qx9Iz/r8/aOB/jCB+zASBgNVHRMBAf8ECDAGAQH/
AgEBMBYGA1UdJQEB/wQMMAoGCCsGAQUFBwMBMIGtBgNVHR4BAf8EgaIwgZ+ggZww
EYIPZWR1cm9hbS5nd2RnLmRlMDqkODA2MQswCQYDVQQGEwJERTENMAsGA1UECgwE
R1dERzEYMBYGA1UEAwwPZWR1cm9hbS5nd2RnLmRlMAqHCIZMCrj/////MBmBF2Vk
dXJvYW1AZWR1cm9hbS5nd2RnLmRlMAmGB0lOVkFMSUQwBYgDAAAAMBKgEAYDAAAA
oAkMB0lOVkFMSUQwHQYDVR0OBBYEFM5mjk8taJqbgJClyZ4uerb/5guDMAoGCCqG
SM49BAMCA2kAMGYCMQCLyAqbd8bB99q4jHbtc01Pbu610bpzWFZeveer+g9Zlt/3
J5KY/lsNmhyQ24aO8xwCMQDrTY8hO1UE6seP2VwaeAN2wLacUOylibCcHlICJIXm
nUhOIqGi6kqffCk8RJjLX5M=
-----END CERTIFICATE-----
""",
)
