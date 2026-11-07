"""Retrieval-augmented generation: load company files, chunk them, search them.

Pipeline:  knowledge/*.md  ->  documents.py (load + clean)  ->  chunking.py (split)
           ->  bm25.py (keyword index)  ->  knowledge_base.py (search API used by the agent tool)
"""
