from pathlib import Path


MONITORING_DIR = (
    Path(__file__).resolve().parents[1]
)

TEMPLATE_FILE = (
    MONITORING_DIR
    / "alertmanager.yml.tmpl"
)

OUTPUT_FILE = (
    MONITORING_DIR
    / "alertmanager.yml"
)

ENV_FILE = (
    MONITORING_DIR
    / "email"
    / "email.env"
)

EMAIL_PASSWORD_FILE = (
    MONITORING_DIR
    / "secrets"
    / "email_smtp_password.txt"
)


EMAIL_START = "# EMAIL_RECEIVER_START"
EMAIL_END = "# EMAIL_RECEIVER_END"

ROUTES_START = "# NOTIFICATION_ROUTES_START"
ROUTES_END = "# NOTIFICATION_ROUTES_END"


PROVIDER_DEFAULTS = {
    "gmail": {
        "host": "smtp.gmail.com",
        "port": "587",
    },
    "outlook": {
        "host": "smtp-mail.outlook.com",
        "port": "587",
    },
    "microsoft365": {
        "host": "smtp.office365.com",
        "port": "587",
    },
}


TELEGRAM_ROUTES = """\
    # NOTIFICATION_ROUTES_START

    - matchers:
        - severity="critical"
      receiver: critical-receiver

    - matchers:
        - severity="warning"
      receiver: warning-receiver

    # NOTIFICATION_ROUTES_END
"""


EMAIL_ROUTES = """\
    # NOTIFICATION_ROUTES_START

    - matchers:
        - severity="critical"
      receiver: email-receiver

    - matchers:
        - severity="warning"
      receiver: email-receiver

    # NOTIFICATION_ROUTES_END
"""


def load_env_file(
    path: Path
) -> dict[str, str]:

    if not path.exists():
        raise FileNotFoundError(
            f"Environment file not found: {path}"
        )

    values: dict[str, str] = {}

    for raw_line in path.read_text(
        encoding="utf-8"
    ).splitlines():

        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("#"):
            continue

        if "=" not in line:
            continue

        key, value = line.split(
            "=",
            1
        )

        key = key.strip()
        value = value.strip()

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]

        values[key] = value

    return values


def replace_section(
    content: str,
    start_marker: str,
    end_marker: str,
    replacement: str
) -> str:

    start = content.find(
        start_marker
    )

    if start == -1:
        raise ValueError(
            f"Start marker not found: "
            f"{start_marker}"
        )

    end = content.find(
        end_marker,
        start
    )

    if end == -1:
        raise ValueError(
            f"End marker not found: "
            f"{end_marker}"
        )

    end += len(end_marker)

    return (
        content[:start]
        + replacement.rstrip()
        + content[end:]
    )


def remove_section(
    content: str,
    start_marker: str,
    end_marker: str
) -> str:

    return replace_section(
        content,
        start_marker,
        end_marker,
        ""
    )


def resolve_smtp_config(
    config: dict[str, str]
) -> dict[str, str]:

    provider = (
        config
        .get(
            "EMAIL_PROVIDER",
            "custom"
        )
        .strip()
        .lower()
    )

    if provider not in {
        "gmail",
        "outlook",
        "microsoft365",
        "custom",
    }:
        raise ValueError(
            "Unsupported EMAIL_PROVIDER: "
            f"{provider}. "
            "Use gmail, outlook, "
            "microsoft365, or custom."
        )

    defaults = PROVIDER_DEFAULTS.get(
        provider,
        {}
    )

    smtp_host = (
        config.get(
            "SMTP_HOST",
            ""
        ).strip()
        or defaults.get(
            "host",
            ""
        )
    )

    smtp_port = (
        config.get(
            "SMTP_PORT",
            ""
        ).strip()
        or defaults.get(
            "port",
            ""
        )
    )

    smtp_username = (
        config
        .get("SMTP_USERNAME", "")
        .strip()
    )

    smtp_from = (
        config
        .get("SMTP_FROM", "")
        .strip()
    )

    smtp_to = (
        config
        .get("SMTP_TO", "")
        .strip()
    )

    missing = []

    if not smtp_host:
        missing.append("SMTP_HOST")

    if not smtp_port:
        missing.append("SMTP_PORT")

    if not smtp_username:
        missing.append("SMTP_USERNAME")

    if not smtp_from:
        missing.append("SMTP_FROM")

    if not smtp_to:
        missing.append("SMTP_TO")

    if missing:
        raise ValueError(
            "Missing email configuration: "
            + ", ".join(missing)
        )

    return {
        "provider": provider,
        "host": smtp_host,
        "port": smtp_port,
        "username": smtp_username,
        "from": smtp_from,
        "to": smtp_to,
    }


def substitute_email_values(
    content: str,
    smtp: dict[str, str]
) -> str:

    replacements = {
        "${SMTP_HOST}":
            smtp["host"],

        "${SMTP_PORT}":
            smtp["port"],

        "${SMTP_USERNAME}":
            smtp["username"],

        "${SMTP_FROM}":
            smtp["from"],

        "${SMTP_TO}":
            smtp["to"],
    }

    for placeholder, value in replacements.items():

        content = content.replace(
            placeholder,
            value
        )

    return content


def generate_config() -> None:

    config = load_env_file(
        ENV_FILE
    )

    if not TEMPLATE_FILE.exists():
        raise FileNotFoundError(
            f"Template not found: "
            f"{TEMPLATE_FILE}"
        )

    template = TEMPLATE_FILE.read_text(
        encoding="utf-8"
    )

    email_enabled = (
        config
        .get(
            "EMAIL_ENABLED",
            "false"
        )
        .strip()
        .lower()
        == "true"
    )

    if not email_enabled:

        template = remove_section(
            template,
            EMAIL_START,
            EMAIL_END
        )

        template = replace_section(
            template,
            ROUTES_START,
            ROUTES_END,
            TELEGRAM_ROUTES
        )

        print(
            "Email notifications: DISABLED"
        )

    else:

        if not EMAIL_PASSWORD_FILE.exists():
            raise FileNotFoundError(
                "Email is enabled but "
                "SMTP password secret was not found: "
                f"{EMAIL_PASSWORD_FILE}"
            )

        smtp = resolve_smtp_config(
            config
        )

        template = substitute_email_values(
            template,
            smtp
        )

        template = replace_section(
            template,
            ROUTES_START,
            ROUTES_END,
            EMAIL_ROUTES
        )

        print(
            "Email notifications: ENABLED"
        )

        print(
            f"Email provider: "
            f"{smtp['provider']}"
        )

        print(
            f"SMTP host: "
            f"{smtp['host']}"
        )

        print(
            f"SMTP port: "
            f"{smtp['port']}"
        )

    OUTPUT_FILE.write_text(
        template,
        encoding="utf-8"
    )

    print(
        f"Generated: {OUTPUT_FILE}"
    )


if __name__ == "__main__":

    try:
        generate_config()

    except Exception as exc:

        print(
            f"ERROR: {exc}"
        )

        raise