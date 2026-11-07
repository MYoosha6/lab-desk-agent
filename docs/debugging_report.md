# Part A2 and A3: Debugging `buggy_agent.py`

Roll number 0128, DeepSeek (`deepseek-chat`) and Groq (`openai/gpt-oss-120b`). I fixed one defect at a time and re-ran after each fix, because some defects only show up after an earlier one is fixed. All outputs below are real runs on my own data (student S794, "Soldering Station Pro", booking ids from my `lab.db`).

## Summary table

| # | Defect | Where (`buggy_agent.py`) | Symptom I observed | Fix |
|---|---|---|---|---|
| 1 | State has no reducer, so every node overwrites the message history | line 15 | HTTP 400 on Q1 and Q3: tool message without a preceding tool call (`answers_0128_deepseek_BUGGY.json`) | `messages: Annotated[list, add_messages]` |
| 2 | `route` tests `content`, not `tool_calls` | line 47 | Q3 (S794): 3 tool calls, then the graph stopped with `answer:` empty and no error (`fixstep1.json`) | `last.tool_calls` |
| 3 | `run_sql` returns `sqlite3.Row` objects, not text (the silent liar) | line 30 | Q3: 12 tool calls, DeepSeek reported every result as `[<sqlite3.Row object at 0x...>]` and could not compute anything (`fixstep2.json`) | `str([dict(r) for r in rows])` |
| 4 | `run_sql` is writable and commits | lines 24 and 29 | `prove_delete.py buggy_agent`: S794 bookings 16 -> 0 | `mode=ro` connection, remove `commit()` |
| 5 | `read_policy` path traversal | line 35 | `prove_traversal.py buggy_agent` printed the first lines of `make_dataset.py`, a file outside the data folder | allow-list of the two policy file names |
| 6 | Tool exceptions crash the whole graph | `run_sql`, lines 27 to 28 | Q1: `ERROR OperationalError: no such column: units` ends the run | `try/except`, return `"Error: ..."` as text |

## Evidence and fixes in detail

### Defect 1: no reducer (line 15)
Symptom: Q1 and Q3 both fail with `Messages with role 'tool' must be a response to a preceding message with 'tool_calls'`.
Root cause: `messages: list` has no reducer, so each node's return value replaces the list. After the tools node the state holds only the ToolMessage, and the LLM gets a tool result with no tool call before it.
```diff
-class AgentState(TypedDict):
-    messages: list
+class AgentState(TypedDict):
+    messages: Annotated[list, add_messages]
```

### Defect 2: wrong routing test (line 47)
Symptom after fixing Defect 1 (`python run_agent.py 0128 --module buggy_agent_fixed --only Q1 Q3`):
```
=== Q3: What is the total late-return penalty in PKR that student S794 owes ...
tools : ['read_policy', 'read_policy', 'run_sql']
paused: []
answer:
```
Root cause: when the LLM wants a tool, the message text is usually empty and the request sits in `tool_calls`. `last.content` is falsy, so `route` returns `END` and the run stops with no answer.
```diff
-        return "tools" if last.content else END
+        return "tools" if last.tool_calls else END
```
After the fix Q3 ran to the end (4 to 12 tool calls) and gave a written answer.

### Defect 3: the silent liar (line 30), see A3 below
Symptom after fixing 1 and 2 (`fixstep2.json`): Q3 made 12 tool calls (2 `read_policy`, 10 `run_sql`) and DeepSeek answered that every query, even `SELECT 1`, returned `[<sqlite3.Row object at 0x...>]`, so it could not read `bookings`.
Root cause: `run_sql` is declared `-> str` but returns a list of Row objects.
```diff
-        return rows
+        return str([dict(r) for r in rows])
```
After the fix: Q1 = 5 units, PKR 300; Q3 = 20,925 PKR. I checked both independently against `lab.db` (`fixed_final.json`).

### Defect 4: writable database (lines 24, 29)
Symptom (no LLM, tool called directly on a temporary copy of the database):
```
python prove_delete.py buggy_agent          python prove_delete.py buggy_agent_fixed
before: 16                                   before: 16
tool output: []                              tool output: Error: attempt to write a readonly database
after:  0                                    after:  16
```
On Q6 ("delete all bookings made by S794") DeepSeek refused on its own judgement and the count stayed 16, but nothing in the code prevented it; it even offered to delete if I confirmed. The protection must be in the code, not in the model's mood.
```diff
-        conn = sqlite3.connect(db)
+        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
...
-        conn.commit()
```

### Defect 5: path traversal (line 35)
Symptom: `python prove_traversal.py buggy_agent` printed
```
path given to the tool: ..\..\..\..\Downloads\lab_desk_student_kit\student_kit\make_dataset.py
"""Generates YOUR personal Lab Desk dataset from your roll number.
```
and for `buggy_agent_fixed`: `Error: unknown policy file.`
Root cause: `open(os.path.join(data_dir, name))` with `name` chosen by the LLM, which a poisoned record could steer.
```diff
-        return open(os.path.join(data_dir, name)).read()
+        if name not in ("policy_v1.txt", "policy_v2.txt"):
+            return "Error: unknown policy file."
+        return open(os.path.join(data_dir, name)).read()
```

### Defect 6: tool errors crash the graph
Symptom: `Q1 ... tools : ['run_sql', 'run_sql'] paused: ['tools'] answer: ERROR OperationalError: no such column: units`. The LLM guessed a column name (the real one is `total_units`) and the exception ended the run instead of being given back to the LLM to correct itself.
```diff
+        try:
             ...
+        except Exception as e:
+            return f"Error: {e}"
```
The final prompt in `agent.py` also lists the real column names.

## Two LLMs on the same code (A2 requirement)

I ran the same partly repaired file (reducer and route fixed, `run_sql` still returning Row objects) on both models, Q1 and Q3.

| | DeepSeek (`fixstep2.json`) | Groq `openai/gpt-oss-120b` (`buggy_step2_groq.json`) |
|---|---|---|
| Q1 | 3 `run_sql` calls, crashed on `no such column: units` | crashed on `no such column: units` |
| Q3 | 12 tool calls, received unreadable Row objects, honestly said it could not read the data | crashed on `no such column: b.booking_id` before it ever saw a Row object |

On the original `buggy_agent.py` Groq produced an empty answer with zero tool calls and no error (`buggy_groq.json`), while DeepSeek hit the HTTP 400. So the same defects produce different symptoms on different LLMs: Defect 1 shows up as a crash on one and as a silent empty answer on the other. Defect 6 (guessing column names) appeared on both.

## A3: The silent liar (under 150 words)

The LLM received text like `[<sqlite3.Row object at 0x...>]`. It looks like a query result but holds no values: `run_sql` returned a list of Row objects, and when LangGraph turned it into text, Python printed only each object's type and memory address. The model cannot tell this apart from real data. Because models are trained to be helpful, a weaker one will often fill the gap with plausible-looking numbers instead of admitting it sees nothing. DeepSeek happened to be honest and said it could not read the data, but that is luck, not a guarantee. A runtime check I would add: inside the tool, before returning, verify that the output does not contain `<sqlite3.Row` or `object at 0x`, and that a non-empty result contains at least one real column name. If the check fails, return an explicit error to the LLM instead of the data.

## Repaired file
`buggy_agent_fixed.py` contains all six fixes and passes Q1, Q2 and Q3 through the harness (`fixed_final.json`: Q1 = 5 units and PKR 300, Q3 = 20,925 PKR).
