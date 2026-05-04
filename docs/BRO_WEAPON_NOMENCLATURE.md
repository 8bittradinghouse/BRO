# BRO Weapon Nomenclature

## Purpose
This file preserves BRO-local house names for the major strategy execution
tools so future modular splits are easier to remember, discuss, and map.

These names are meant to improve:
- system memory,
- modular design mapping,
- operator shorthand,
- team-specific identity.

They are not runtime authority by themselves.

## Authority Boundary
- Canonical runtime/config/doctrine terms remain:
  - `maker`
  - `taker`
  - `sniper`
  - stage and validator names already present in BRO doctrine/code
- This file provides mnemonic aliases only.
- If a code path, validator, event payload, or config key uses canonical
  maker/taker/sniper naming, that remains authoritative unless a separate
  reviewed packet changes it explicitly.

## House Mapping
### `Masamune Energy Blade Set`
Umbrella house name for the active BRO strike-tool family.

Current members:
- `Taker Katana`
- `Sniper Wakazashi`
- `Solar Slug Maker Cannon`

### `Taker Katana`
House alias for the main taker commitment weapon.

Meaning:
- canonical normal taker authority,
- decisive strike lane,
- commitment-style execution logic,
- truth surfaces used to judge whether the taker weapon sees, submits, and
  rides cleanly.

Current doctrine note:
- in current canonical live doctrine, accepted normal taker authority is hard
  `<=7s`
- this alias does not broaden that authority

### `Sniper Wakazashi`
House alias for the shorter-window specialist strike expression.

Meaning:
- the close-in fast specialist blade,
- near-expiry strike framing,
- diagnostic or future dedicated specialist language when the shorter strike
  layer needs to be discussed separately from the broader taker weapon.

Current doctrine note:
- current canonical runtime authority may collapse much of this specialist
  meaning into the same hard `<=7s` taker commitment law
- do not infer a separate implemented weapon system unless code/doctrine proof
  says so

### `Solar Slug Maker Cannon`
House alias for the maker execution weapon.

Meaning:
- passive emplacement / quote engine,
- heavier deliberate fire,
- the maker-side execution tool that pressures the market through bounded
  positioning instead of close-in commitment strikes.

Current doctrine note:
- this maps to maker execution logic, maker competitiveness, quote quality, and
  maker-specific friction surfaces
- it does not authorize maker use in stages where canonical doctrine forbids it

## Usage Rule
Good usage:
- “The `Solar Slug Maker Cannon` is getting blunted by quote-quality skips.”
- “The `Taker Katana` still needs cleaner proof windows.”
- “A future split should keep `Sniper Wakazashi` surfaces easy to trace.”

Bad usage:
- renaming config keys, validator names, or event taxonomies ad hoc
- using alias language to hide scope drift or make doctrine sound more mature
  than the implementation has earned

## Modular Design Value
These names are worth keeping because they:
- preserve the BRO/Gundam house styling,
- make future module splits easier to remember,
- give uniquely ours names to major subsystems,
- help future schoolhouse/training material map tools to roles quickly.

Plain-English:
this is memory architecture for the shop, not decorative lore.
