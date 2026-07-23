# Auto-updating NL/UK/USA EPG for TiviMate

This repo automatically rebuilds a corrected XMLTV EPG file every day,
matching epgshare01's channel data to your playlist's exact tvg-ids
(fixes the "NPO.1.nl" vs "npo1.nl" mismatch that breaks bulk EPG matching).

## Setup (one-time)

1. Create a new **public** GitHub repo and upload all files in this folder
   (including the hidden `.github/workflows/update-epg.yml` file — make
   sure your upload method preserves that folder structure).
2. Go to your repo's **Actions** tab. If prompted, click "I understand my
   workflows, go ahead and enable them."
3. Click into the "Update EPG" workflow → **Run workflow** (manual trigger)
   to generate the first version immediately, rather than waiting for the
   next scheduled run.
4. Once it finishes (green checkmark), `combined_epg.xml` will appear in
   your repo root.

## Using it in TiviMate

Point TiviMate's EPG source at:
```
https://raw.githubusercontent.com/<your-username>/<repo-name>/main/combined_epg.xml
```

Since the file content updates daily but the URL never changes, TiviMate
will keep pulling fresh data on its normal update schedule with no
further action needed from you.

## Updating your channel list

If your playlist's channels change (provider updates, new channels added),
regenerate `channels_reference.json` and re-upload it — ask Claude to
rebuild it from a fresh copy of your playlist.

## Files

- `build_epg.py` — the script that downloads epgshare01 data, matches it
  to your playlist, and writes `combined_epg.xml`
- `channels_reference.json` — your playlist's tvg-id/tvg-name pairs for
  NL/UK/USA (no credentials, safe to keep in a public repo)
- `.github/workflows/update-epg.yml` — the daily automation schedule
