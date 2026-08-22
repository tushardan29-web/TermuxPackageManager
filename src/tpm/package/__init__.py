"""Package module: backend abstraction."""

from .backend import AptBackend, PackageManagerBackend, detect_backend

__all__ = ["AptBackend", "PackageManagerBackend", "detect_backend"]
