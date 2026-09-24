"""Product-neutral environment provider contract."""

from abc import ABCMeta, abstractmethod


class EnvironmentProvider(object, metaclass=ABCMeta):
    @abstractmethod
    def setup(self):
        pass

    @abstractmethod
    def clean(self):
        pass

    @abstractmethod
    def status_text(self):
        pass

    @abstractmethod
    def start(self):
        pass

    @abstractmethod
    def restart(self):
        pass

    @abstractmethod
    def stop(self):
        pass

    def heal(self):
        """Self-heal environment, restarting instances and repairing broken standbys."""
        return self.start()
