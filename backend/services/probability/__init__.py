"""Universal probability translators (ECDF is the initial implementation).

`from services.probability.ecdf import UniversalECDFProbability, get_universal_ecdf`
"""
from services.probability.ecdf import (  # noqa: F401
    UniversalECDFProbability,
    ECDFPrediction,
    get_universal_ecdf,
    reset_universal_ecdf_singleton,
)
