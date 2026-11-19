# Affiliate publication quality / SEO consistency (2026-11-19)

This is a **static-site build and publish-validation** change only.
The registered-IP Windows Rakuten collector, locked API call count, immutable
price history, Affiliate ID maintenance continuity proof, note editorial and
site publication scheduling remain unchanged. No GA4 measurement ID is assumed.

## 1. Prevent false publication failures from divergent comparison rules

`build_site.py` withholds a conditional comparison page when too few **safe,
actually comparable** products remain after excluding category mismatches,
multiple-price variants, expired/upcoming API listing windows. Previously
`validate.py` counted raw matches and could demand pages that the builder
correctly did not generate. This could block all Pages publication.

`validate.py` now reuses `build_site.item_matches_comparison` and
`build_site.comparison_price_is_uncertain`, plus
`affiliate_quality.displayable/recommendable`, before deciding which
comparison pages *must* exist. The test covers both an absent and present
eligible comparison page.

## 2. Ensure every rendered affiliate link preserves attribution

In the existing **build -> validate -> Pages upload** workflow, a read-only
HTML parser scans generated pages. Every affiliate anchor must:

- point to an **exact, unmodified** URL in its corresponding current source
  product row. The audit decodes HTML entities but never follows URLs;
- have matching public product code and category metadata;
- specify a nonblank placement for optional GA4 click event;
- retain `rel="sponsored nofollow noopener"`, `target="_blank"` and the
  official Rakuten affiliate host/path.

Unexpected, stripped, wrong-product or unmarked affiliate links fail
publication **before** upload. Diagnostics use fixed codes; no tracking URL,
secret, source item name, or raw HTML is printed.

The upstream Windows `OutputPolicy` still separately checks authenticity
against an authenticated Rakuten API response. The HTML audit is not a
replacement for that origin check and makes no claim about conversion.

## 3. Canonical URLs / social sharing metadata

Every generated active HTML page receives its own **absolute,
self-referential** `rel="canonical"` URL identical to the URL listed in the
generated `sitemap.xml`. `og:title`, `og:description`, `og:type` and
`og:url` are added for social previews. No unverifiable product photos
or ratings are introduced into metadata.

Publish validation rejects absent/mismatched canonical link annotations
rather than silently serving conflicting URLs.

Reference: https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls

## Validation

```shell
python -X utf8 -m unittest discover -s scripts -p "test_*.py" -v
```

No external API, note.com post, local credentials, or GitHub Pages deployment
is needed for the offline regression suite. Production takes effect on the
**next actual eligible Pages deployment** that builds from the merged main
commit. A GitHub Actions run with overall `success` but skipped Deploy
Pages is not evidence of new publication.

GA4's gadget-site stream ID remains empty; event transmission remains OFF.
