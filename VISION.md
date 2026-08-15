Each panel should answer one question.

AstroFrame UI Principles

• Be calm.
• Be concise.
• State what is happening.
• Never hide important work.
• Never use technical jargon unless it belongs in the Solver Log.
• The Solver Log explains how.
• The status bar explains what.
## Flexible Collection Import

**Design principle: AstroFrame adapts to the user's data; the user should not have to restructure data to suit AstroFrame.**

Collection importers detect known source formats, locate the actual target table even when title, attribution, notes or legends precede it, and map source columns into AstroFrame's common Knowledge Engine model. Useful source-specific metadata is preserved without cluttering the normal interface. Unknown formats should ultimately receive a proposed column mapping and require user input only where meaning is genuinely ambiguous. An import preview should show the detected collection, table location, target count and recognised columns before committing the import.

Importers understand spreadsheets; the Knowledge Engine does not. Once imported, every source becomes a normal AstroFrame Collection.
