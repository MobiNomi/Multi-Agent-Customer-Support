"""Step 3: BM25 keyword search, the classic ranking formula behind most search engines.

For each query word, a chunk scores higher when:
- the word appears in it (more times = better, with diminishing returns: k1),
- the word is rare across all chunks (rare words carry more meaning: IDF),
- the chunk is short (a match in a short chunk counts more than in a long one: b).
"""
import math
import re
from collections import Counter
from typing import List, Sequence

import snowballstemmer

from app.rag.synonyms import QUERY_SYNONYMS

_STEMMER = snowballstemmer.stemmer("english")
_TOKEN = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset("""
a an and are as at be been but by can could did do does for from had has have how i if in into is it
its me my of on or our so that the their them then there these they this to was we were what when
where which who why will with would you your yours am get got
""".split())


def tokenize(text: str) -> List[str]:
    """Lowercase, split into words, drop filler words, reduce words to their stem (refunded -> refund)."""
    words = [w for w in _TOKEN.findall(text.lower()) if w not in STOPWORDS]
    return _STEMMER.stemWords(words)


def tokenize_query(text: str) -> List[str]:
    """Like tokenize, plus synonyms for words customers use but the documents don't (yearly -> annual)."""
    extra = " ".join(QUERY_SYNONYMS[w] for w in _TOKEN.findall(text.lower()) if w in QUERY_SYNONYMS)
    return tokenize(f"{text} {extra}")


class BM25Index:
    def __init__(self, documents: Sequence[List[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.doc_freqs = [Counter(doc) for doc in documents]
        self.doc_lens = [len(doc) for doc in documents]
        self.avg_len = (sum(self.doc_lens) / len(self.doc_lens)) if documents else 0.0
        n = len(documents)
        df = Counter(term for doc in documents for term in set(doc))
        self.idf = {term: math.log(1 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()}

    def scores(self, query_tokens: List[str]) -> List[float]:
        results = []
        for freqs, length in zip(self.doc_freqs, self.doc_lens):
            score = 0.0
            for term in set(query_tokens):
                tf = freqs.get(term)
                if not tf:
                    continue
                norm = 1 - self.b + self.b * length / (self.avg_len or 1)
                score += self.idf[term] * tf * (self.k1 + 1) / (tf + self.k1 * norm)
            results.append(score)
        return results
