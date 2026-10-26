"""canonkeeper — series-continuity engine for long-running fiction.

Pipeline:  corpus → chapterize → extract claims (Muse Spark) → canon store
           → candidate conflicts (local heuristics) → adjudicate (Muse Wonder)
           → report.
"""

__version__ = "0.1.0"
