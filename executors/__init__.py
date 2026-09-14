"""Approval and execution plane. One job and one credential per executor. Every action reversible.

Nothing here runs without an approvals row in status 'approved' (Section 13.6). The dispatcher reads
approved rows; executors receive the row and refuse anything else.
"""
