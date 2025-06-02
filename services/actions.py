#!/usr/bin/env python3
"""
gemma_crm_terminal.py

A terminal demo: Gemma will ask one clear follow-up question
for each missing field, then finally output the JSON + action.
"""

import json
import ollama

MODEL = "gemma3:4b"

SYSTEM_PROMPT = """
You are the assistant for Daily CRM.
Given a user’s instruction, choose exactly one of these actions:
  • add_client
  • track_revenue
  • create_invoice
  • log_interaction
  • schedule_event

Each action’s required parameters:
  add_client: name, email, phone
  track_revenue: client_id, amount, date
  create_invoice: client_id, price, due_date
  log_interaction: client_id, interaction_type, notes, date
  schedule_event: client_id, event_type, date, time, location

Behavior:
1) If any required parameter is missing, ask exactly one follow-up question
   requesting that specific piece of information.  
   **E.g.:** “Sure—what is the new client’s email address?”  
2) Only once you have _all_ required parameters, output **only** the JSON:
   {
     "action": "<action_name>",
     "parameters": { … }
   }
3) Do not list parameters as a comma-separated list, and do not output any
   extra text beyond the question or final JSON.
"""

def main():
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        user_input = input("You: ").strip()
        if not user_input:
            continue
        messages.append({"role": "user", "content": user_input})

        resp = ollama.chat(model=MODEL, messages=messages)
        gm = resp["message"]["content"].strip()

        # Try parsing JSON; if it works, we're done
        try:
            cmd = json.loads(gm)
            action = cmd.get("action", "<unknown>")
            print(f"\nAction: {action}")
            print("JSON:")
            print(json.dumps(cmd, indent=2))
            break
        except json.JSONDecodeError:
            # Not JSON → must be a follow-up question
            print(f"Gemma: {gm}")
            messages.append({"role": "assistant", "content": gm})
            # loop for user's answer

    print("\nDone.")

if __name__ == "__main__":
    main()