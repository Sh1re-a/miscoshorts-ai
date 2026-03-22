__all__ = ["app"]


def __getattr__(name):
	if name == "app":
		from .server import app as flask_app

		return flask_app
	raise AttributeError(f"module 'app' has no attribute {name!r}")