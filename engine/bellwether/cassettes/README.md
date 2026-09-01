Recorded provider responses, written by `BELLWETHER_PROVIDER=record` and served
back by `BELLWETHER_PROVIDER=replay`.

A cassette is keyed by the model, the agent tag, the exact prompt and system
text, the output schema, and the sample index, so a replayed run is byte for
byte the run that was recorded. That is what makes a live run reproducible after
the fact: a UI bug reported against a particular answer can be reproduced
offline, and a prompt change can be diffed against a fixed baseline instead of
against noise.

None are committed. Recording one costs real tokens, and a cassette carries the
full prompt, so committing a set is a deliberate decision about what ends up in
the repository rather than a side effect of running the tests.
