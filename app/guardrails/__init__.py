"""Guardrails: plain-code safety checks around the AI.

    input    sensitive.py  hide card / CNIC / password / CVV / IBAN before anything is sent or saved
             injection.py  stop attempts to override the assistant's instructions
    router   off-topic and unsafe requests get a fixed reply, with no agent call
    action   ticket limit and duplicate-ticket check (in the tool executor and handoff)
    output   output.py     remove made-up citations, false refund promises, prompt leaks, internal names

Every check that fires is recorded as a note (shown in the chat page's details panel) and as a row
in the guardrail_events table, so attacks and mistakes can be reviewed later.
"""
