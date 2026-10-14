Daily XP Limit
==============
- Each account has a daily cap of 40,000 EXP gained during its current daily cycle.
- The cycle resets at 07:00 Bangladesh time (UTC+06:00).
- When the cap is reached, that account's worker is paused automatically.
- At the next 07:00 reset, an auto-paused account resumes automatically.
- A manually paused account stays paused after the daily reset until the user presses Resume.
- Deleting an account removes its daily-limit state. Re-adding it starts a new daily state.
- daily_limits.json stores daily XP counters and pause flags. Keep this file in the same working directory as main-fix.py and dashboard_server.py.
- This build was syntax-checked and its ZIP integrity was checked. A live game-server session was not run, so verify the first account with the dashboard and terminal logs.
