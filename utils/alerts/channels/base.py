class AlertChannel:
    """A delivery channel. send() must return bool and must not raise."""

    name = "base"

    def configured(self) -> bool:
        raise NotImplementedError

    def send(self, subject: str, body: str) -> bool:
        raise NotImplementedError
