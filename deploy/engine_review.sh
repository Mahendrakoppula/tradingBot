#!/bin/bash
# Nightly engine review (spec §69-§71, §79), run by trading-bot-engine-review.timer
# at 15:45 IST after the session: today's daily review plus the paper-to-live
# gates over the trailing 28 days, both posted to the TECH Telegram chat.
# Read-only over the journal. Exit code is the gates' verdict (1 = not ready),
# which systemd records; it is informational, nothing acts on it.
set -uo pipefail
cd /opt/trading-bot
set -a; . ./.env; set +a
TODAY=$(TZ=Asia/Kolkata date +%F)
SINCE=$(TZ=Asia/Kolkata date -d "28 days ago" +%F)
.venv/bin/python -m trading_bot.research_cli review --from "$TODAY" --to "$TODAY" --telegram
.venv/bin/python -m trading_bot.research_cli gates --from "$SINCE" --to "$TODAY" --telegram
# candidate refinements over the trailing 28 days: counts + counterfactuals, never applied (sections 71/83)
.venv/bin/python -m trading_bot.research_cli refinements --from "$SINCE" --to "$TODAY" --telegram
