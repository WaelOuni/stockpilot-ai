"""Ingest only current synthetic candidate facts using the configured real embedding model."""
from scripts.candidate.runtime import api_python


def main():
    result = api_python('''
import asyncio
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.embeddings.ollama_embedding_adapter import OllamaEmbeddingAdapter
from stockpilot.infrastructure.persistence.postgres.pgvector_knowledge_repository import PgVectorKnowledgeRepository
from stockpilot.application.use_cases.ingest_knowledge import IngestKnowledgeUseCase
async def ingest():
    settings=Settings()
    embedding=OllamaEmbeddingAdapter(settings)
    repository=PgVectorKnowledgeRepository(settings.postgres_url)
    use=IngestKnowledgeUseCase(embedding,repository)
    for sku in ('D-C','S-A','W-A','C-SAND-1'):
        p=db.products.find_one({'sku':sku})
        text=f"{p['name']}\\nSKU: {p['sku']}. Catégorie: {p['category']}. Prix catalogue numérique: {p['price']}. Stock instantané: {p['stock']}. Aucune spécification technique vérifiée."
        await use.execute(content=text,source='candidate-v133-'+sku)
asyncio.run(ingest())
rows=pg.execute('SELECT count(*),min(vector_dims(embedding)),max(vector_dims(embedding)) FROM knowledge_chunks').fetchone()
print(json.dumps({'knowledge_rows':rows[0],'dimensions_min':rows[1],'dimensions_max':rows[2]}))
''')
    print(result)


if __name__ == '__main__':
    main()
