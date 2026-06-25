# Anime franchise grouping

## Product behavior

For MAL anime detail pages, the grouping behavior is:

- a fixed `Series` block built from franchise continuity TV-line data;
- dynamic secondary sections assigned by ordered rule packs:
  - `Main Story Extras`
  - `Specials`
  - `Spin Offs`
  - `Alternatives`
  - `Related Series`

The feature is guarded by `ANIME_FRANCHISE_GROUPING_ENABLED` and only applies to MAL anime detail pages.

## Example layout

A large franchise can be split into clearer groups instead of one flat related-anime list.

Example:

```text
Series
  Main TV entry
  Sequel TV entry

Main Story Extras
  Main-story movie
  Main-story OVA

Specials
  Recap episode
  Short special

Spin Offs
  Substantial spin-off series

Alternatives
  Alternative version

Related Series
  Residual related entry
```

The exact section depends on MAL relation data, anime format, runtime, anchors, and the ordered rule packs. Sections appear only when they have entries.


## Franchise payload cache model

MAL anime franchise payloads are cached as display-ready read models.

Cache keys:

- `mal_anime_franchise_<media_id>` stores a direct payload.
- `mal_anime_franchise_alias_<media_id>` stores a lightweight alias to a canonical payload.

Expected states for a media id:

- `DIRECT`: a direct payload exists and no alias exists.
- `ALIAS`: no direct payload exists and an alias points to a canonical payload.
- `MISS`: no direct payload and no alias exist.
- `DIRECT + ALIAS`: invalid state; the direct payload would shadow alias resolution.

Direct payloads are valid for canonical roots, local mini-franchises, and non-aliasable satellites that need their own detail-page context. An alias is not required for every entry that appears in a franchise payload.

Aliases are only created for media ids explicitly listed in `aliasable_media_ids`.

`covered_media_ids` means the payload knows about the media id and may display it somewhere in the read model. `aliasable_media_ids` means the media id is allowed to resolve to that payload through an alias.

## Runtime path

The active UI grouping path is:

```text
AnimeFranchiseService
 -> AnimeFranchiseSnapshotService
 -> AnimeFranchiseUiPipeline
 -> SeriesBuilder
 -> UiCandidateAssembler
 -> RulePipeline
 -> LayoutCompiler
 -> ViewModelAdapter
 -> cache/context preparation
 -> media_details.html
```

For the broader app architecture, see `docs/architecture-overview.md`.

## Snapshot input

Grouping consumes snapshot facts such as:

- `series_line`
- `direct_candidates`
- `promoted_continuity_candidates`
- `no_series_line_secondary_candidates`
- `root_story_parent_candidates`
- `canonical_root_media_id`
- `fallback_anchor_media_id`
- `has_series_line`

For the full snapshot field reference and invariants, see `docs/anime-franchise-snapshot.md`.

## Series block

`SeriesBuilder` is intentionally narrow:

- reads only `snapshot.series_line`;
- keeps `Series` separate from dynamic sections;
- stores real MAL titles and media metadata;
- does not invent display labels.

If there is no `series_line`, the fixed `Series` block has no entries. Secondary candidates may still render if rules classify them into visible sections.

## Secondary sections

Candidate assembly excludes fixed series entries and keeps provenance metadata for rules:

- relation types;
- source media IDs;
- whether a relation came from the series line or root;
- promoted continuity metadata;
- no-series ordering metadata;
- root-story-parent metadata.

Current visible section titles defined by section metadata include:

- `Main Story Extras` (`continuity_extras`)
- `Specials` (`specials`)
- `Spin Offs` (`spin_offs`)
- `Alternatives` (`alternatives`)
- `Related Series` (`related_series`)

`Spin Offs` and `Alternatives` are visible refinement sections when candidates meet the current rules. They are still dynamic sections, not part of the fixed `Series` contract. `ignored` is internal and marked not visible.

## UI grouping is not import selection

UI grouping and import selection are separate projections built from the same snapshot.

```text
Snapshot fact
    ↓
UI grouping decision
    ↓
Visible section
```

A visible section is the result of UI policy, not a canonical franchise fact.

A franchise entry can appear in a visible UI section without being imported automatically. Import profiles decide what is useful enough to create in a user's library; UI grouping decides how already-known franchise entries should be displayed on a detail page.

Do not use UI sections as direct import-profile input unless the import policy explicitly evolves to do so.

## Detail-page grouping is not Anime Series View grouping

The detail-page franchise layout and Anime Series View card grouping are separate projections.

The detail page may show `alternative_version` or `alternative_setting` entries in the dynamic `Alternatives` section when the UI rule pipeline classifies them there.

Anime Series View does not use those alternative relations to merge cards or choose canonical roots. For Series View grouping, alternatives remain separate unless they are connected through another groupable relation such as `prequel`, `sequel`, `parent_story`, `full_story`, `side_story`, or `spin_off`.

Do not use the detail-page `Alternatives` section as proof that entries should share one Series View card.

## Badges and tooltips

Franchise entries can display small badges that summarize useful relation and format information directly on the detail page.

These badges are presentation helpers. They make entries easier to scan, but they do not decide section placement.

Typical badge information includes:

- normalized relation label;
- anime format/media type;
- relation source tooltip when available;
- current-entry marker when the entry is the page being viewed.

Placement still belongs to the UI rule pipeline. Badge and tooltip preparation belongs to request/context rendering helpers.

## Rule engine

Current rule-pack order from `presets/default.py`:

1. `base_facts`
2. `base_placement`
3. `relation_rules`
4. `secondary_refinement_rules`
5. `anchor_rules`
6. `format_rules`
7. `section_rules`

This is not a global first-match-wins pipeline. A later pack can refine or override `candidate.section_key`. When a section changes, the engine appends a `placement_trace` entry with pack, rule, previous section, next section, and whether it was initial placement or an override.

`section_rules` should remain metadata-focused: titles, order, and hidden-if-empty behavior. It should not become a placement policy layer.


## Rule engine design

The grouping pipeline is implemented as ordered rule packs. Each pack owns a specific responsibility and should not perform work that belongs to another pack.

The pipeline is not a global first-match-wins classifier. Earlier packs can assign initial facts or placement, and later packs can refine or override `candidate.section_key`. Any section change should remain traceable through `metadata["placement_trace"]`.

The intended flow is:

```text
facts
 -> initial placement
 -> relation classification
 -> secondary refinement
 -> anchor gating
 -> format/runtime gating
 -> section metadata
```

## Placement lifecycle

```text
Candidate discovered
        ↓
base_facts
        ↓
normalized facts available

base_placement
        ↓
fallback section assigned

relation_rules
        ↓
relation-based placement

secondary_refinement_rules
        ↓
optional refinement

anchor_rules
        ↓
candidate may be accepted or rejected

format_rules
        ↓
candidate may be accepted or excluded

section_rules
        ↓
section metadata applied
```

Not every candidate changes at every step. Some packs only enrich candidate metadata, some assign an initial section, some refine an existing section, some filter by moving a candidate to `ignored`, and the final section pack only applies section metadata.

## Override model

Rule packs do not all have the same authority. Some packs create information, some classify candidates, some refine existing classification, some can only filter visibility, and some can only add metadata.

| Pack | Can place | Can refine | Can exclude | Can add metadata |
| --- | --- | --- | --- | --- |
| `base_facts` | No | No | No | Yes |
| `base_placement` | Yes | No | No | Yes |
| `relation_rules` | Yes | Yes | No | Yes |
| `secondary_refinement_rules` | No | Yes | No | Yes |
| `anchor_rules` | No | No | Yes | Yes |
| `format_rules` | No | No | Yes | Yes |
| `section_rules` | No | No | No | Yes |

A section can therefore be overwritten by later classification or refinement packs, but final metadata should not move candidates. Filtering packs should answer whether an already-classified candidate is allowed to appear.

## Why the pack order exists

The order is intentional, not arbitrary.

### Facts before placement

Relation and provenance facts must exist before any reliable placement decision can be made. This keeps later packs from reimplementing low-level relation checks.

### Placement before refinement

A candidate needs an initial section before refinement can make it more precise. `base_placement` guarantees a predictable fallback, then relation rules provide the first meaningful business placement.

### Refinement before filtering

The pipeline classifies first, then decides whether the classified candidate is allowed to appear. This keeps visibility constraints separate from relation semantics.

### Filtering before metadata

Visible sections are determined before titles, ordering, and hidden-if-empty behavior are stabilized. `section_rules` should describe sections, not reclassify entries.

## Placement vs refinement

Placement answers:

```text
Where would this candidate belong?
```

Refinement answers:

```text
Can we place it more precisely?
```

`relation_rules` and `secondary_refinement_rules` are separated on purpose. Relation rules perform broad business classification; secondary refinement can promote broad related-series candidates into specialized buckets such as substantial spin-offs or alternatives. Keeping refinement separate prevents relation classification from becoming a monolithic ruleset and makes specialized secondary categories easier to evolve independently.

## Why refinement exists

`relation_rules` answers the broad question:

```text
Which family does this entry belong to?
```

`secondary_refinement_rules` answers the narrower question:

```text
Can this broad placement be made more precise?
```

Examples:

```text
related_series -> spin_offs
related_series -> alternatives
specials -> related_series
```

This keeps the first relation classification readable while allowing specialized secondary sections to evolve independently.

## Placement vs filtering

Placement determines the intended section. Filtering determines whether the candidate is allowed to appear.

### `anchor_rules`

These rules ask:

```text
Is this candidate allowed from this anchor?
```

They should not answer where the candidate belongs. They preserve directness and accepted-anchor constraints after placement/refinement has already happened.

### `format_rules`

These rules ask:

```text
Is this format allowed?
```

They should not answer where the candidate belongs. They keep format/runtime visibility decisions independent from relation classification.

## Reading placement_trace

`placement_trace` is the decision log for section changes. A typical trace can look like:

```text
fallback placement
        ↓
relation placement
        ↓
secondary refinement
        ↓
anchor or format filtering
```

Use it first when a candidate appears in an unexpected section or disappears into `ignored`. It shows which pack and rule changed `candidate.section_key`, whether the change was initial placement or an override, and what the previous and next section values were.

## Rule pack responsibilities

### `base_facts.py`

Owns normalized candidate facts used by later packs.

Responsibilities:

- populate relation/provenance-derived helper signals;
- normalize candidate metadata into reusable facts;
- keep later packs from recomputing low-level relation checks.

Must not:

- assign final product section policy;
- define section metadata;
- perform template/display work.

### `base_placement.py`

Owns base section definitions and initial fallback placement.

Responsibilities:

- declare available section definitions;
- set initial fallback placement for unclassified candidates;
- keep default placement predictable before relation-specific rules run.

Must not:

- implement detailed relation-specific business rules;
- apply format/runtime filtering.

### `relation_rules.py`

Owns relation-driven classification.

#### Why this pack exists

This pack performs the first meaningful business classification. It turns normalized relation signals into broad UI sections before specialized refinement or filtering happens.

Responsibilities:

- classify candidates based on normalized relation signals;
- use `relation_types` and relation facts when a candidate has more than one relation signal;
- assign coarse business sections such as continuity extras, specials, or related-series candidates.

Must not:

- define visual titles/order;
- perform cache/import decisions.

### `secondary_refinement_rules.py`

Owns refinement of broad secondary placement.

#### Why this pack exists

This pack refines broad secondary placement without turning relation classification into a monolithic ruleset. It lets specialized secondary categories evolve independently from coarse relation semantics.

Responsibilities:

- refine broad related-series candidates into more specific dynamic sections when the current policy allows it;
- handle substantial spin-offs, alternatives, or other refined secondary buckets;
- keep refinement separate from the first coarse relation classification.

Must not:

- bypass anchor or format rules;
- turn UI refinements into import-profile behavior.

### `anchor_rules.py`

Owns directness and anchor gating.

#### Why this pack exists

This pack separates relation semantics from visibility constraints. A candidate can have a valid relation-based section but still be rejected if it is not connected through an accepted UI anchor.

Responsibilities:

- enforce whether a candidate is allowed from a series-line anchor, fallback/root anchor, or promoted continuity path;
- reject candidates that are not connected through an accepted UI anchor;
- preserve the distinction between direct candidates and deliberately promoted transitive exceptions.

Must not:

- redefine relation semantics;
- define section titles/order.

### `format_rules.py`

Owns conservative format/runtime exclusions.

#### Why this pack exists

This pack keeps visibility and content-policy decisions independent from relation classification. It prevents unsupported formats from leaking into sections that were otherwise valid by relation.

Responsibilities:

- ignore excluded MAL formats such as `cm` and `pv`;
- apply section-specific MAL format gates;
- apply runtime/episode thresholds when the UI policy requires them.

Must not:

- define section metadata;
- introduce user-specific behavior.

### `section_rules.py`

Owns section metadata only.

Responsibilities:

- set section titles;
- set section ordering;
- set visibility / hidden-if-empty policy;
- keep section metadata stable for the adapter/template.

Must not:

- move candidates between sections;
- implement relation, anchor, or format business policy.

## Section policy

Current coarse policy:

- `other`-only relations are ignored.
- Continuity (`prequel`, `sequel`) secondary entries go to `Main Story Extras` unless later format rules reject them.
- `side_story`, `summary`, and `full_story` are treated as special-related signals, except root-story-parent handling.
- Spin-offs and alternatives start in related-series logic and can be refined into `Spin Offs` or `Alternatives`.
- `cm` and `pv` are ignored.
- Specials are constrained to specific MAL formats.
- Indirect candidates without an accepted anchor are ignored.

## Special cases

- **No `series_line`**: candidates can anchor to `fallback_anchor_media_id`; no fixed `Series` entries are shown.
- **Non-TV root**: direct anchors can include the root in addition to TV series-line nodes.
- **Specials / recaps / summaries**: relation and format rules decide display; import profiles can exclude summary targets.
- **Direct `full_story` to TV parent**: non-TV roots can expose a root-story-parent candidate and route it to related series.
- **No-series continuity**: prequel/sequel continuity can be sorted through no-series metadata.
- **Transitive non-TV continuity**: promoted continuity candidates allow non-TV prequel/sequel chains to be considered beyond direct neighbors.
- **Substantial spin-offs**: TV spin-offs with enough episodes/runtime and long movies can be refined into `Spin Offs`.
- **Alternatives**: `alternative_version` and `alternative_setting` can be refined into `Alternatives` with stable sort rank.
- **Direct vs transitive candidates**: direct candidates come from anchors; promoted continuity candidates are deliberately scoped transitive exceptions.

## Cache interaction

The view uses `anime_franchise_cache.load_payload_for_media(media_id)`:

- fresh valid payload: render prepared franchise context;
- stale valid payload: render it and queue refresh if cooldown allows;
- miss: keep normal related-anime fallback visible and queue a build;
- invalid payload: ignore it, mark error metadata, and queue rebuild;
- valid but non-displayable payload: keep fallback visible.


## Ownership summary

Grouping policy belongs in rule packs. Candidate assembly preserves facts, layout remains structural, adapter remains compatibility-only, request context adds user-specific data, and templates render only.

Legacy fields such as `series_label` may exist only for cached-payload compatibility. They are not current product behavior.

For the broader layer map, see `docs/architecture-overview.md`.

## Relation signals

The UI pipeline may carry several relation-related fields. They are not interchangeable.

### `relation_type`

Compatibility facade for a single representative normalized relation. Useful for simple display and legacy-compatible payload shapes, but it should not be treated as the only source of truth when a candidate has multiple relation signals.

### `relation_types`

Richer normalized set/list of relation signals for a candidate. Used by rule packs when placement depends on ambiguous or multi-origin relations.

### `metadata["origins"]`

Detailed provenance captured by candidate assembly. Useful for debugging, relation-source resolution, and targeted heuristics. It should not become a broad standalone placement system unless the rule policy explicitly evolves that way.

### `metadata["placement_trace"]`

Debug trace appended when rule packs assign or change `candidate.section_key`. Useful to understand whether a candidate was initially placed, refined, ignored, or moved by a later pack.

## Change discipline

When changing grouping behavior:

1. Identify the owner layer first: facts, placement, relation classification, secondary refinement, anchor gating, format gating, metadata, adapter, or rendering.
2. Change rule packs before considering view/template changes.
3. Keep `Series` sourced only from `snapshot.series_line`.
4. Keep layout structural-only.
5. Keep adapter compatibility-only.
6. Keep user-specific enrichment out of cached payloads.
7. Update tests and docs with the behavior change.

## What should not be done here

- Do not classify franchise entries in templates.
- Do not force sections in `views.py`.
- Do not mix import profile rules with UI placement rules.
- Do not duplicate MAL relation normalization in UI code.
- Do not write user-specific status/progress into cached payloads.

## Difference from Anime Series View

Anime franchise grouping is the MAL anime detail-page feature: it explains one MAL franchise around the current detail-page entry.

Anime Series View is the Anime list feature: it groups the user's tracked Anime library into franchise cards.

Both features use the franchise snapshot, but their projections and templates are separate. Do not use detail-page UI sections as a direct source for Anime Series View behavior.
