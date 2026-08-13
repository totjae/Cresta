# Console read models cover the legacy in-process Paper ledger and the
# broker-authoritative Kiwoom mock-account projection. The tuple is explicit
# so a future live account cannot leak into MOCK views by accident.
CONSOLE_MOCK_ACCOUNT_ALIASES = ("PAPER", "KIWOOM_MOCK_PRIMARY")
