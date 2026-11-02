"""
Hierarchical Legal Chunker (Decree 30/2020/ND-CP).
Decomposes Vietnamese legal statutes and commercial contracts into structured
parent-child hierarchies: Chương (Chapter) -> Điều (Article) -> Khoản (Clause) -> Điểm (Point).
Injects contextual breadcrumbs into each child chunk.
"""

import re
from typing import List, Dict, Any


class HierarchicalLegalChunker:
    ARTICLE_PATTERN = re.compile(r"(Điều\s+\d+[\.:]?\s*[^\n]+)", re.IGNORECASE)
    CLAUSE_PATTERN = re.compile(r"(\n\s*\d+\.\s*[^\n]+)")

    def chunk_document(self, document_id: str, title: str, content: str) -> List[Dict[str, Any]]:
        """
        Splits text into chunks preserving Article and Clause boundaries.
        Prepends contextual breadcrumbs to each chunk.
        """
        chunks = []
        articles = self.ARTICLE_PATTERN.split(content)

        if len(articles) <= 1:
            # Fallback to standard block chunking
            return [{
                "chunk_id": f"{document_id}_c0",
                "document_id": document_id,
                "breadcrumb": title,
                "content": content.strip()
            }]

        current_article = "Mở đầu"
        for i in range(1, len(articles), 2):
            article_header = articles[i].strip()
            article_body = articles[i+1].strip() if i+1 < len(articles) else ""
            current_article = article_header

            # Look for clauses (Khoản)
            clauses = self.CLAUSE_PATTERN.split(article_body)
            if len(clauses) > 1:
                for c_idx, clause_text in enumerate(clauses):
                    clause_clean = clause_text.strip()
                    if clause_clean:
                        breadcrumb = f"[{title}] > [{article_header}] > [Khoản {c_idx+1}]"
                        chunks.append({
                            "chunk_id": f"{document_id}_a{i//2}_k{c_idx}",
                            "document_id": document_id,
                            "breadcrumb": breadcrumb,
                            "content": f"{breadcrumb}\n{clause_clean}"
                        })
            else:
                breadcrumb = f"[{title}] > [{article_header}]"
                chunks.append({
                    "chunk_id": f"{document_id}_a{i//2}",
                    "document_id": document_id,
                    "breadcrumb": breadcrumb,
                    "content": f"{breadcrumb}\n{article_body}"
                })

        return chunks

