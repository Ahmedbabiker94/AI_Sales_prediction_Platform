from __future__ import annotations

from abc import ABC, abstractmethod

from src.data.contracts.document import DocumentDatasetContent, DocumentMetadata


class OCRProvider(ABC):
    """Provider boundary for OCR engines.

    Concrete providers are intentionally not selected at the architecture
    stage. The rest of the platform depends only on this contract.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return a stable provider name."""

    @property
    def version(self) -> str:
        """Return the provider version used for processing."""
        return "unknown"

    @abstractmethod
    def process(
        self,
        document: DocumentMetadata,
    ) -> DocumentDatasetContent:
        """Extract canonical document elements from a document."""
        raise NotImplementedError
