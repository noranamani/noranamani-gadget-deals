#!/usr/bin/env python3
"""Read-only deadline warning for the Windows-authorized affiliate continuity.

Never validates, refreshes or manufactures an affiliate proof. It only reports
the expiration already published in data/collection-status.json.
"""
import argparse
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path


def evaluate(document, now=None, warning_hours=72):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("NOW_TZ_REQUIRED")
    try:
        expiry_raw = document["affiliateVerificationExpiresAt"]
        if not isinstance(expiry_raw, str):
            raise ValueError("INVALID_EXPIRY")
        expiry = datetime.fromisoformat(expiry_raw.replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            raise ValueError("NAIVE_EXPIRY")
        verified = document.get("affiliateVerified") is True
        remaining = expiry - now
        if not verified:
            return {"state": "UNVERIFIED", "alert": True, "expiry": expiry.isoformat(),
                    "remaining_hours": round(remaining.total_seconds()/3600, 2)}
        if remaining <= timedelta(0):
            state = "EXPIRED"
        elif remaining <= timedelta(hours=warning_hours):
            state = "EXPIRING"
        else:
            state = "OK"
        return {"state": state, "alert": state != "OK", "expiry": expiry.isoformat(),
                "remaining_hours": round(remaining.total_seconds()/3600, 2)}
    except (KeyError, ValueError, TypeError, OverflowError):
        return {"state": "UNVERIFIABLE", "alert": True,
                "expiry": None, "remaining_hours": None}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--file",default="data/collection-status.json")
    parser.add_argument("--warning-hours",type=int,default=72)
    args=parser.parse_args()
    if not (1 <= args.warning_hours <= 168):
        parser.error("warning-hours must be 1..168")
    try:
        data=json.loads(Path(args.file).read_text(encoding="utf-8"))
        result=evaluate(data,warning_hours=args.warning_hours)
    except (OSError,ValueError,TypeError):
        result={"state":"UNVERIFIABLE","alert":True,
                "expiry":None,"remaining_hours":None}
    print("AFFILIATE_CONTINUITY_OBSERVATION "+json.dumps(result,ensure_ascii=False,sort_keys=True))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"],"a",encoding="utf-8") as f:
            f.write("alert="+str(result["alert"]).lower()+"\n")
            f.write("state="+result["state"]+"\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"],"a",encoding="utf-8") as f:
            f.write("## Read-only affiliate verification continuity\n\n"
                    f"State: **{result['state']}**; recorded expiry: {result['expiry']}; "
                    f"remaining hours: {result['remaining_hours']}.\n\n"
                    "This does NOT renew verification or change any website/product feed.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
