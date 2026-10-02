# Voice baseline (TTFA)

1. Open Settings > System > Response speed, speak each utterance once in a quiet room, then note the TTFA p50 / p95 shown.
2. Fill the "before" columns now; re-run the same 20 utterances after each voice task and add a row to the summary.
3. Mark misrecognised = Y if Sara's transcript differs from what you said; keep notes short.

| # | utterance | language | expected path | TTFA p50 before | TTFA p95 before | misrecognised (Y/N) | notes |
|---|-----------|----------|---------------|-----------------|-----------------|---------------------|-------|
| 1 | open chrome | English | regex | | | | command |
| 2 | volume badha do | Hinglish | regex | | | | command |
| 3 | समय क्या हुआ है | Hindi | regex | | | | command |
| 4 | set a reminder for 6 pm gym | English | regex | | | | command |
| 5 | kal subah 7 baje alarm laga do | Hinglish | regex | | | | command |
| 6 | मौसम कैसा है | Hindi | regex | | | | command |
| 7 | play music | English | regex | | | | command |
| 8 | wifi band kar do | Hinglish | regex | | | | command |
| 9 | open notepad and then open downloads | English | planner | | | | command, two steps |
| 10 | screenshot lo | Hinglish | regex | | | | command |
| 11 | what is the difference between RAM and ROM | English | llm | | | | question |
| 12 | भारत की राजधानी क्या है | Hindi | llm | | | | question |
| 13 | python mein list aur tuple mein kya farak hai | Hinglish | llm | | | | question |
| 14 | explain how a transformer model works | English | llm | | | | question |
| 15 | mujhe photosynthesis simple bhasha mein samjhao | Hinglish | llm | | | | question |
| 16 | Sara stop (say it 2 s into a long answer) | English | regex | | | | interrupt |
| 17 | रुको (say it mid-answer) | Hindi | regex | | | | interrupt |
| 18 | ruk jao, nahi chahiye (say it mid-answer) | Hinglish | regex | | | | interrupt |
| 19 | Sara, never mind (say it while a command runs) | English | regex | | | | interrupt |
| 20 | Sara, new question: what is the time (say it mid-answer) | English | regex | | | | interrupt, barge-in with new command |

## After later voice tasks

| task | TTFA p50 | TTFA p95 | misrecognised (of 20) | notes |
|------|----------|----------|-----------------------|-------|
| T13 | | | | |
| T14 | | | | |
| T15 | | | | |
| T16 | | | | |
| T17 | | | | |