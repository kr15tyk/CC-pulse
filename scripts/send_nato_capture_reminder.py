# Sends a weekly reminder email prompting a human to manually visit the
# NATO NIAPCL Cisco-filtered product search, copy the visible product text,
# and submit it via the "NATO Cisco Baseline Update" GitHub Issue template.
#
# This script never contacts ia.nato.int itself -- it only sends a reminder
# email. Triggered weekly by .github/workflows/nato_capture_reminder.yml.
from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import config

REMINDER_RECIPIENT = "kriknowl@cisco.com"
NATO_SEARCH_URL = config.NATO_NIAPCL_URL
ISSUE_TEMPLATE_URL = (
    "https://github.com/kr15tyk/CC-pulse/issues/new?template=nato-cisco-update.yml"
)

SUBJECT = "NATO Cisco Baseline -- Friday capture reminder"

HTML_BODY = f"""
<html>
  <body style="font-family: sans-serif; line-height: 1.5;">
    <h2>NATO Cisco Baseline -- Friday capture reminder</h2>
    <p>It is time for this week's manual NATO NIAPCL Cisco baseline check. Steps:</p>
    <ol>
      <li>Open the NATO NIAPCL Cisco-filtered product search:<br>
          <a href="{NATO_SEARCH_URL}">{NATO_SEARCH_URL}</a></li>
      <li>On page 1, select all the listed products and copy the visible text.</li>
      <li>If there is a page 2, repeat the same select-all-and-copy for it.</li>
      <li>Note the total product count shown on the page 
          (the "N product(s) registered for this manufacturer" text).</li>
      <li>Open the intake issue form and paste in the capture date, total count, 
          and the copied text for each page:<br>
          <a href="{ISSUE_TEMPLATE_URL}">{ISSUE_TEMPLATE_URL}</a></li>
      <li>Submit the issue -- diffing, baseline update, and any new-Cisco-listing 
          celebration alert happen automatically from there.</li>
    </ol>
    <p>Reminder: please do not use "View Page Source" for page 2+ -- it does not 
    reflect pagination. Select the visible rendered text instead.</p>
  </body>
</html>
"""


def main() -> None:
    password = os.environ.get("CC_EMAIL_PASSWORD", config.EMAIL_PASSWORD)
    if not password:
        raise SystemExit("No email password configured -- aborting reminder send.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = SUBJECT
    msg["From"] = config.EMAIL_FROM
    msg["To"] = REMINDER_RECIPIENT
    msg.attach(MIMEText(HTML_BODY, "html"))

    with smtplib.SMTP(config.EMAIL_SMTP_HOST, config.EMAIL_SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(config.EMAIL_USERNAME, password)
        smtp.sendmail(config.EMAIL_USERNAME, [REMINDER_RECIPIENT], msg.as_string())
    print(f"Reminder email sent to {REMINDER_RECIPIENT}")


if __name__ == "__main__":
    main()
