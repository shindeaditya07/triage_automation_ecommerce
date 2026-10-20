# Policy retrieval

Which passages of `docs/policies.md` a draft may draw on. The draft step (a later branch) calls
`retrieve()` once per ticket and stores the returned IDs in `drafts.retrieved_passage_ids`.

## The index (`services/api/retrieval/index.py`)

`policy_embeddings` holds one row per policy ID and policy version: the passage text, its vector
and the embedding model's name. Policies and FAQ entries are all passages (`**RET-1.1 ...**`,
`**FAQ-10.4 ...**`), so one parser (`policy_source.parse_passages`) feeds both the index and the
`/readyz` check.

`make index-policies` (`scripts/index_policies.py`) builds it. It needs only `DATABASE_URL` and the
LLM settings, and prints counts, never text.

- A passage whose stored text and model both match is left alone, so running it again embeds
  nothing and costs nothing.
- A changed passage, or a changed `EMBED_MODEL`, is re-embedded in place.
- A new policy version adds rows beside the old ones; search only looks at the current version.
- An embedder that reports a different model name than the one configured is refused, because
  search looks passages up by the name the query was embedded with.

`/readyz` (already in place) is 503 until every passage of the current version is indexed, and
names the problem: empty index, only an older version, or N passages missing.

## Retrieval (`services/api/retrieval/retrieve.py`)

`retrieve(index, llm, subject=, body=, intent=, extra_ids=)` returns:

1. **Required passages**, in this order: `TON-9.2` (never promise an outcome), then the passages
   `catalog.BY_INTENT` lists for the intent (a damage report always carries DMG-3.1 to 3.4), then
   `extra_ids` (a suggestion-only draft passes `ESC-8.3`). Source `intent`.
2. **The 4 closest by meaning** that are not already there, best first, with their cosine
   similarity. Source `vector`.

Fail closed: an ID the catalog names that is not a passage of the loaded policy file, or is
missing from the index, raises `RetrievalError`. A model failure on the embedding call raises its
`LLMError`. Either way the caller escalates; nothing is drafted from a partial set.
`catalog.unknown_policy_ids` is the "cited IDs must exist" check the grounding step reuses.

The message text is embedded as it stands after intake, which has already masked card, account and
phone numbers. It goes to the embedding provider.

## What the smoke eval says, and does not

`make eval-smoke` now reports retrieval recall on the labeled tickets that get a draft: the share
of cited passages (tone passages excluded) that were retrieved, with and without the vector
search. Smoke mode embeds with the fake client's hashed bag-of-words vectors, so the
intent-required half is a real measurement and the vector half only checks the wiring. The catalog
is written from what each kind of ticket needs under the policy, not tuned to these numbers; what
it misses (for example a refund ticket citing CAN-5.1) is what the vector search is for.

**"Is the answer in the FAQ?" is not decided here.** I tried a similarity threshold on the
product-question tickets: the one the FAQ cannot answer (T052) scored 0.37, above two that it can
(T048 0.30, T050 0.36) with these vectors. A real embedding model may separate them better, but a
threshold picked without one would be a guess dressed as a rule. The draft step gets the FAQ
passages and the prompt tells the model to say the owner will follow up when the answer is not in
them; whether to also route that ticket suggestion-only is decided there, against a live model.
