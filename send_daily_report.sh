#!/bin/sh
# Daily production report email for lab-chemical (no-op until SMTP configured in Admin > Email Settings)
docker exec lab-chemical-prod python -c "
import sys; sys.path.insert(0, \"/app\")
from app import create_app
app = create_app()
r = app.test_cli_runner().invoke(args=[\"send-daily-report\"])
print(r.output)
" >> /var/local/lab-Chemical-analesis/daily_report_cron.log 2>&1
