"""Isolated, READ-ONLY forensic: would a "level broke -> cut" exit rule have
flipped the lead's payoff geometry?

Consumes ONLY the reconstructed trades from the sibling
``direction_location_forensics.loader`` (which reads the production journals
read-only). Writes nothing back to the live path; all output goes under
``research/exit_cut_forensics/out/``. Never imports axonai/ or MT5, never sends
or modifies an order. Pure measurement.
"""
