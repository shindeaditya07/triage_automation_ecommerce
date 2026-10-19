"""Seed data for the 31 mock-store edge cases (spec section 17).

Every date is computed from `now`, so the same scenarios exist whatever the clock says: tests pin
it with FROZEN_NOW, staging uses the real time. Building the seed twice with the same `now` gives
identical data, which is what makes `make seed` idempotent.
"""

from .build import Scenario, SeedData, build_seed

__all__ = ["Scenario", "SeedData", "build_seed"]
