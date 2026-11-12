# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Reference source extraction.

The knowledge base is built from documents, not typed by hand:

* ``chart``  - the Minich vitamin/mineral interactions PDF (text layer, read with pypdf)
* ``matrix`` - the Biological conflicts matrix image (reviewed transcription, validated
  against the image fingerprint)

Both produce the same normalised rows, which :mod:`nutrient_conflicts.refsources.merge` folds
into ``nutrient_interactions.csv``.
"""

from .builder import ReferenceBuilder, ReferenceBuildError
from .chart import ChartRow, extract_chart
from .matrix import MatrixRow, load_matrix
from .merge import MergedPair, merge_claims
from .names import NutrientNameMap

__all__ = [
    "ChartRow",
    "MatrixRow",
    "MergedPair",
    "NutrientNameMap",
    "ReferenceBuildError",
    "ReferenceBuilder",
    "extract_chart",
    "load_matrix",
    "merge_claims",
]
