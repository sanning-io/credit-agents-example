"""Code shared by both services: parsing settings, the case store, and the
evidence wiring. Sharing this code does not join the two services: they
still run as separate processes and talk only over HTTP. Nothing here holds
a value from more than one service's own settings."""
