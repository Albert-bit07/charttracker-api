import logging
from pathlib import Path

logger = logging.getLogger(__name__)
ASSET_DIRECTORY = Path(__file__).resolve().parents[2]


def notify(title: str, message: str, timeout: int = 10):
    try:
        from plyer import notification
        notification.notify(title=title, message=message, timeout=timeout)
    except Exception:
        logger.warning("Desktop notification unavailable", exc_info=True)


def playsound(result: str):
    """Audio is optional; a headless server must still be able to track prices."""
    try:
        import pygame
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        filename = "Win.mp3" if result == "win" else "Lose.mp3"
        pygame.mixer.music.load(str(ASSET_DIRECTORY / filename))
        pygame.mixer.music.play()
    except Exception:
        logger.warning("Audio notification unavailable", exc_info=True)
