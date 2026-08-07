"""Command line entry point for repeatable local knowledge indexing."""

import argparse

from personal_agent.knowledge.embedding import (
    HashEmbeddingProvider,
    SentenceTransformersEmbeddingProvider,
)
from personal_agent.knowledge.retrieval import PersonalKnowledgeService
from personal_agent.knowledge.store import KnowledgeStore


def main() -> None:
    parser = argparse.ArgumentParser(description="建立 Personal Agent 本地知识索引")
    parser.add_argument("source_directory")
    parser.add_argument("--database", required=True)
    parser.add_argument("--embedding-model", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--hash-embedding", action="store_true", help="只用于本地合同测试")
    args = parser.parse_args()

    provider = (
        HashEmbeddingProvider()
        if args.hash_embedding
        else SentenceTransformersEmbeddingProvider(args.embedding_model, device=args.device)
    )
    store = KnowledgeStore(args.database)
    try:
        documents = PersonalKnowledgeService(store, provider).index_directory(args.source_directory)
        print(f"indexed_documents={documents} indexed_chunks={store.count_chunks()}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
