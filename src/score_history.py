"""Scores recorded before a rule change, kept so they cannot be overwritten.

Round 1 of the unseen-variant test was scored on the first version of the
rules (Phase 3). The rules were then improved using what round 1 showed, so
re-running round 1 today gives a better score that is no longer a fair test.
This is the original result, exactly as measured.
"""

# rule_id: (planted, caught, missed, flags raised, false positives)
UNSEEN_ROUND1_BEFORE_FIXES = {
    "R01": (5, 2, 3, 3, 1),
    "R02": (4, 0, 4, 0, 0),
    "R03": (3, 3, 0, 7, 4),
    "R04": (2, 2, 0, 2, 0),
    "R05": (5, 2, 3, 2, 0),
    "R06": (2, 2, 0, 2, 0),
    "R07": (1, 1, 0, 1, 0),
    "R08": (3, 2, 1, 2, 0),
    "R09": (3, 3, 0, 11, 8),
}
