"""Verification package exposing the 4-layer validation chain, classifier, and kill gate."""

from src.verification.classifier import ErrorClassifier
from src.verification.layer1_syntax import Layer1SyntaxValidator
from src.verification.layer2_morphology import Layer2MorphologyValidator
from src.verification.layer3_solver import Layer3AdversarialSolver
from src.verification.layer_expander import AnswerSetExpander
from src.verification.layer_topic_leak import TopicLeakValidator
from src.verification.pipeline import BatchVerificationReport, VerificationPipeline

__all__ = [
    "VerificationPipeline",
    "BatchVerificationReport",
    "Layer1SyntaxValidator",
    "TopicLeakValidator",
    "Layer2MorphologyValidator",
    "Layer3AdversarialSolver",
    "AnswerSetExpander",
    "ErrorClassifier",
]
