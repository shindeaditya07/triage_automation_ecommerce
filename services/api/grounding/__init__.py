"""The grounding validator: the deterministic check that decides whether a draft may be shown.

See docs/grounding.md. `validate` is a pure function: a reply, its citations and what it was
written from go in, a list of named rule failures comes out. It reads no clock, no database and no
network, and it calls no model.
"""
