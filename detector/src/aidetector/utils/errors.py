class ConfigError(ValueError):
    """config.json cannot be used. Kept apart from the config module, which
    loads config.json when it is imported."""


class MissingConfigError(ConfigError, FileNotFoundError):
    """There is no config.json next to the program."""
