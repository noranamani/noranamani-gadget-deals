# Affiliate quality and optional GA4 measurement

The Windows collector is still the sole source of Rakuten item observations.
The daily site build applies a **read-only presentation gate** to old/current
product data. This never deletes historical prices, guesses variant prices or
changes Rakuten-provided tracking URLs.

- Mismatched products that no longer match conservative `category_relevance`
  are not presented, even if an earlier daily generation contained them.
- Listings with multiple selectable storage sizes, power ratings or battery
  capacities can still appear in the product grid with a clear disclaimer, but
  must not be ranked as comparable prices, deals, editor picks or price drops.
- API listing start/end metadata may stop premature/expired promotions, but
  is **not** a claim that a coupon is valid. Merchant title promotions are
  explicitly labeled as unverified.
- Source classification and ranking remain read-only, preserving Windows
  scheduled collection, daily editorial, and weekly note workflows.

## Activate GA4

Set `config/analytics.json` `ga4_measurement_id` to the **new gadget
site's own** GA4 web stream ID (`G-...`) and rebuild through the existing
guarded publishing path. Do not borrow another site's stream, invent an ID,
or publish cookies before the site's analytics disclosure is appropriate.
An empty ID is intentionally safe/off.

When enabled, pages load the standard Google tag. All affiliate anchors
carry item code, category and link placement in data attributes. One
`affiliate_click` event is sent on a real user's click only when gtag is
available; outgoing URLs remain untouched and navigation is not blocked.
Parameters are `item_code`, `item_category`, `link_placement`,
`link_target=rakuten`. Events are not sales: compare to the Rakuten report.

GA4 enhanced measurement may separately record outbound `click` events;
do not sum those events and `affiliate_click` as if they were different users.
The event deliberately **does not send the affiliate URL** as a parameter.

## Affiliate identity confirmation (Windows only)

The proof file is **not** an OAuth token or a GitHub secret. It verifies
the entered ID against Windows Credential Manager and expires 30 days
after the last actual official-account check. It must never be auto-renewed.

Open https://webservice.rakuten.co.jp/account_affiliate_id/ with the signed-in
owner account. On the Windows production PC, from an interactive PowerShell
session, use:

```powershell
$pipelineHome = Join-Path $env:USERPROFILE 'GadgetPipeline\windows-production'
$source = Get-Content (Join-Path $pipelineHome 'code-source.json') -Raw | ConvertFrom-Json
$script = Join-Path $pipelineHome ("code\" + $source.sourceCommit + "\scripts\windows_credentials.py")
& 'C:\Python312\python.exe' -X utf8 $script --verify-affiliate (Join-Path $pipelineHome 'state\affiliate-proof.json')
```

Only paste the ID at the hidden prompt. Never copy the ID to chat, Git,
logs, environment files or screenshots. A fresh proof is written only on
a confirmed match. This does not invoke the API or trigger publication.

To inspect the expiry **without renewing it**, run the same script after
reloading the updated code on the production PC:

```powershell
& 'C:\Python312\python.exe' -X utf8 $script --check-affiliate-proof (Join-Path $pipelineHome 'state\affiliate-proof.json')
```

A valid proof prints the new `expiresAt` timestamp in UTC and the word
`valid`. Invalid, changed-credential or expired proof reports only a
fixed non-secret error and exits nonzero. Do not run scheduled collection
outside its designated time window just to check this record.
