# Documentary reads and current business facts

An explicit request to summarize or explain company documents must not become
a stock lookup just because it contains “disponibles”, a low-stock phrase or a
product category. Previously, `extract_criteria()` retained the whole documentary
question as a product reference and its availability flag selected `STOCK_CHECK`
before retrieval, even when the classifier returned `UNKNOWN`.

The tenant assistant now recognizes a bounded French/English documentary read
before the broad catalogue/stock heuristics. The exact historical request and
its English equivalent are regression fixtures. This is not a general rule for
the words “documentation” or “available”, nor a fallback for every `UNKNOWN`.

| Request | Authority / behavior |
|---|---|
| Explicit summary/explanation of documents or procedures | Existing scoped `TenantRetrieval` |
| Document discussion of stock rules or cancellation procedures | Documentary read, no business action |
| Ordinary catalogue/product/availability lookup | Existing catalogue/stock paths |
| Documentary request plus a second business request | Clarification, no partial execution |
| Documentary request for current stock/price, or identified SKU availability | Clarification; documents cannot supply live values |
| Exact historical document-price question | Existing current-catalogue-price route retains priority |
| Explicit ORDER request | Existing lexical quantity, identity, permission and confirmation contract |
| Historical source/history/confirmation request | Existing exact route retains priority |

Discovery and execution use the same recognition on the original user question.
A model-supplied intent cannot activate this documentary override. The helper
does not change the original message, `read_only`, model, prompt, permission,
tenant filters or persistence contract. The normal retrieval block is shared,
including source identities, generation/chunk hashes and citation revalidation.

The grammar is intentionally limited. Unrecognized language retains its existing
handling; it does not gain a new write path. Compound or ambiguous requests
should be split explicitly. The literal current-price regression G15-08 and the
original 301-prompt corpus remain unchanged.

Validation must distinguish a generated factual answer with actual citations
from an abstention and from the appended list of consulted documents. A success
requires citations in the answer body and corresponding persisted `cited_sources`;
the provenance appendix alone is not evidence of a cited answer.

The routing tests include a hostile classifier and `read_only` both true and false.
Disposable-store integrations check tenant scope, current authorization, durable
source identities and no ORDER mutation. These tests do not replace real Ollama,
browser and destination qualification before release.
