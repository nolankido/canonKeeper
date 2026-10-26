# Benchmark corpora

Public-domain series, fetched from Project Gutenberg at run time (nothing is redistributed
here). Each folder holds a `series.json` manifest in reading order.

| corpus | books | why it is here |
|---|---|---|
| `sherlock-holmes/` | 6 (Gutenberg #244, 2097, 1661, 834, 2852, 108) | Famous *real* continuity errors to find unassisted: Watson's Jezail-bullet wound moves from shoulder (*A Study in Scarlet*) to leg (*The Sign of the Four*); Watson is "John" and once "James"; Mrs. Hudson is once "Mrs. Turner". Good baseline for the eval harness because the pre-existing contradictions are documented. |

Candidates to add: L. Frank Baum's Oz books (long series, many characters, sloppy canon),
L. M. Montgomery's Anne books (tight canon, good for a low-false-positive check),
Edgar Rice Burroughs' Barsoom novels.

Run:

```bash
canonkeeper fetch    evals/corpora/sherlock-holmes/series.json
canonkeeper estimate evals/corpora/sherlock-holmes/series.json
canonkeeper build    evals/corpora/sherlock-holmes/series.json
canonkeeper audit    evals/corpora/sherlock-holmes/series.json
canonkeeper eval     evals/corpora/sherlock-holmes/series.json --n 20 --seed 0
```
