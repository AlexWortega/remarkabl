import re
import threading

import numpy as np

from .config import Settings
from .storage import Store


class Search:
    def __init__(self, store: Store, settings: Settings):
        self.store, self.settings = store, settings
        self.model = None
        self.lock = threading.Lock()

    def encode(self, texts, query=False):
        if self.settings.embedding_mode == "lexical":
            return [None] * len(texts)
        with self.lock:
            if self.model is None:
                from sentence_transformers import SentenceTransformer

                self.model = SentenceTransformer(
                    self.settings.embedding_model,
                    device="cpu",
                    cache_folder=str(self.settings.data_dir / "models"),
                )
            prefix = "query: " if query else "passage: "
            vectors = self.model.encode(
                [prefix + text for text in texts], normalize_embeddings=True, batch_size=16
            )
        return [np.asarray(vector, dtype=np.float32).tobytes() for vector in vectors]

    def index(self, document_id, fragments):
        vectors = self.encode([fragment.text for fragment in fragments])
        with self.store.connect() as db:
            db.execute("DELETE FROM fragments WHERE document_id=?", (document_id,))
            db.executemany(
                "INSERT INTO fragments(document_id,label,text,embedding) VALUES(?,?,?,?)",
                [
                    (document_id, fragment.label, fragment.text, vector)
                    for fragment, vector in zip(fragments, vectors, strict=True)
                ],
            )
            db.execute(
                "UPDATE documents SET status='indexed',error=NULL WHERE id=?", (document_id,)
            )

    def retrieve(self, query, limit=6):
        terms = re.findall(r"\w+", query, re.UNICODE)[:40]
        if not terms:
            return []
        expression = " OR ".join('"' + term + '"' for term in terms)
        with self.store.connect() as db:
            lexical = db.execute(
                "SELECT rowid FROM fragment_search WHERE fragment_search MATCH ? ORDER BY rank LIMIT 50",
                (expression,),
            ).fetchall()
            rows = db.execute(
                "SELECT f.*,d.title,d.origin FROM fragments f JOIN documents d ON f.document_id=d.id WHERE d.status='indexed'"
            ).fetchall()
        if not rows:
            return []
        score = {}
        for rank, row in enumerate(lexical):
            score[row[0]] = 1 / (60 + rank)
        if self.settings.embedding_mode != "lexical":
            encoded = self.encode([query], query=True)[0]
            q = np.frombuffer(encoded, dtype=np.float32)
            semantic = [
                (row["id"], float(np.dot(q, np.frombuffer(row["embedding"], dtype=np.float32))))
                for row in rows
                if row["embedding"] is not None
            ]
            semantic.sort(key=lambda item: item[1], reverse=True)
            for rank, (fragment_id, similarity) in enumerate(semantic[:50]):
                if similarity >= 0.75:
                    score[fragment_id] = score.get(fragment_id, 0) + 1 / (60 + rank)
        by_id = {row["id"]: dict(row) for row in rows}
        found = []
        for fragment_id in sorted(score, key=score.get, reverse=True)[:limit]:
            row = by_id.get(fragment_id)
            if row:
                row.pop("embedding")
                found.append(row)
        return found
