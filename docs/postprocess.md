# Post-processing

Pipeline order (every stage can be switched off in `[postprocess]`):
`strip → hallucination filter → custom replacements → English terms → ZWNJ → digits → Persian punctuation → capitalize → auto-punctuate`.

## Decisions for Persian (T-3.2.6)

- **Capitalization:** Persian has no case; `capitalize_first` only touches a leading ASCII letter (and is skipped when the text starts with a digit).
- **Sentence-final mark:** `auto_punctuate` appends ASCII `.` — it is what Persian keyboards type and what apps/search handle consistently.
  An existing `.` `!` `?` `؟` `…` `:` `؛` `،` is respected (nothing appended).
- **Question/comma/semicolon:** `?` `,` `;` become `؟` `،` `؛` **only when the text contains Persian letters**; commas inside numbers (`3,500`) are kept.
- **Digits:** `digits = "keep"` (default) leaves what the recognizer returned; `"latin"` / `"persian"` normalize ۰-۹ / ٠-٩ / 0-9.

## ZWNJ (half-space)

Handled: `می`/`نمی` + verb, plural/comparative suffixes written as separate words (`ها`, `های`, `هایی`, `تر`, `ترین`, `تری`, possessive `ام ات اش مان تان شان`).
Not handled (add via `custom_replacements`): compounds like `به‌طور`, `خانه‌ی`, ezafe forms.
Words directly after a pronoun (`من ها`) are never joined.

## English terms

`voicetype/postprocess/terms.py` maps Persian transliterations (پایتون، لینوکس، گیت‌هاب، …) *and* any-case Latin spellings to one canonical form
(`Python`, `Linux`, `GitHub`, …); ~40 terms. Extend with `[postprocess.custom_replacements]`.

## Custom replacements

Keys are literal text; a key starting with `re:` is a regular expression (max 200 chars, nested quantifiers like `(a+)+` are rejected).

```toml
[postprocess.custom_replacements]
"می شود" = "می‌شود"
"re:\\bqt\\d\\b" = "Qt"
```

## Voice commands (opt-in, `voice_commands = true`)

Applied only when the **whole utterance** is the command: «خط جدید» → newline, «پاراگراف جدید», «نقطه» → `.`, «ویرگول» → `،`,
«علامت سوال» → `؟`, «علامت تعجب» → `!`, «دو نقطه» → `:`, «پاک کن» → inject nothing; English: new line, new paragraph, period, comma, question mark.
