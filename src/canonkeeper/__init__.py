"""canonkeeper — series-continuity engine for long-running fiction.

Pipeline:  corpus → chapterize → extract claims (cheap model) → canon store
           → candidate conflicts (local heuristics) → adjudicate (stronger model)
           → report.
"""

__version__ = "0.1.0"
