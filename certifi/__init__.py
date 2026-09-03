"""MIT compatibility facade using the operating system CA bundle."""
from .core import contents, where

__all__ = ["contents", "where"]
__version__ = "system-ca"
