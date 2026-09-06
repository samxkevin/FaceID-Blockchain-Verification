"""Typed errors so every failure mode has a clear, actionable message.

Every exception raised deliberately by this project derives from PipelineError.
Scripts catch PipelineError and print `remedy` so the operator always knows the
next action, instead of reading a raw traceback.
"""

from __future__ import annotations


class PipelineError(Exception):
    """Base class for all deliberate pipeline failures."""

    #: Short, stable machine-readable code (also used in tests).
    code = "PIPELINE_ERROR"

    def __init__(self, message: str, remedy: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.remedy = remedy

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


# --- Input / face stage -------------------------------------------------


class InputImageError(PipelineError):
    code = "INPUT_IMAGE"


class NoFaceDetectedError(PipelineError):
    code = "NO_FACE"


class AmbiguousFaceError(PipelineError):
    code = "AMBIGUOUS_FACE"


# --- Reverse image search stage ----------------------------------------


class ProviderConfigError(PipelineError):
    code = "PROVIDER_CONFIG"


class ProviderRequestError(PipelineError):
    code = "PROVIDER_REQUEST"


class NoSocialMatchError(PipelineError):
    code = "NO_SOCIAL_MATCH"


class ImageTransportError(PipelineError):
    code = "IMAGE_TRANSPORT"


# --- Evidence stage -----------------------------------------------------


class EvidenceError(PipelineError):
    code = "EVIDENCE"


class TamperDetectedError(PipelineError):
    code = "TAMPER_DETECTED"


# --- Blockchain stage ---------------------------------------------------


class ChainConfigError(PipelineError):
    code = "CHAIN_CONFIG"


class ChainConnectionError(PipelineError):
    code = "CHAIN_CONNECTION"


class WrongNetworkError(PipelineError):
    code = "WRONG_NETWORK"


class ContractError(PipelineError):
    code = "CONTRACT"


class TransactionError(PipelineError):
    code = "TRANSACTION"


class RecordNotFoundError(PipelineError):
    code = "RECORD_NOT_FOUND"
