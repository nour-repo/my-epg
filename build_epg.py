#!/usr/bin/env python3
"""
Builds a corrected, combined XMLTV EPG file for a playlist by:
1. Downloading epgshare01's per-country EPG files
2. Matching each EPG channel to the playlist's tvg-id by normalized display name
3. Remapping channel IDs so they exactly match the playlist's tvg-ids
4. Writing a single combined XMLTV file with no duplicate channel IDs

Run with: python3 build_epg.py
Requires: channels_reference.json in the same directory (tvg-id/tvg-name pairs, no credentials)
"""
import re
import json
import gzip
import urllib.request
from collections import defaultdict

SOURCES = {
    "NL": "https://epgshare01.online/epgshare01/epg_ripper_NL1.xml.gz",
    "UK": "https://epgshare01.online/epgshare01/epg_ripper_UK1.xml.gz",
    "USA": "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz",
}

OUTPUT_FILE = "combined_epg.xml"


def normalize(name):
    name = re.sub(r'\|[A-Z]+\|\s*', '', name)
    name = re.sub(r'\b(HD|4K|8K|4ᴋ|8ᴋ|RAW|ʀᴀᴡ|FHD|UHD|SD|VIP)\b', '', name, flags=re.IGNORECASE)
    name = name.replace('+', 'PLUS')
    name = re.sub(r'[^a-zA-Z0-9]', '', name)
    return name.lower()


def download_epg(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    return gzip.decompress(raw).decode("utf-8", errors="ignore")


def build_playlist_index(channel_list):
    """norm_name -> tvg_id, deduped by keeping the first occurrence."""
    index = {}
    for ch in channel_list:
        norm = normalize(ch["tvg_name"])
        if norm and norm not in index:
            index[norm] = ch["tvg_id"]
    return index


def process_country(country, url, playlist_index):
    print(f"[{country}] downloading {url}")
    epg_content = download_epg(url)

    channel_blocks = re.findall(r'(<channel id="([^"]+)">.*?</channel>)', epg_content, re.DOTALL)

    raw_matches = []  # (epg_id, target_tvg_id, display_name)
    for block, epg_id in channel_blocks:
        m = re.search(r'<display-name[^>]*>([^<]+)</display-name>', block)
        if not m:
            continue
        norm = normalize(m.group(1))
        if norm in playlist_index:
            raw_matches.append((epg_id, playlist_index[norm], m.group(1)))

    # Dedupe: if multiple epg_ids map to the same target tvg_id, keep the
    # one whose display name is shortest (closest literal match), drop rest.
    by_target = defaultdict(list)
    for epg_id, target, disp in raw_matches:
        by_target[target].append((epg_id, disp))

    final_matches = {}  # epg_id -> target tvg_id
    for target, entries in by_target.items():
        entries_sorted = sorted(entries, key=lambda x: len(x[1]))
        final_matches[entries_sorted[0][0]] = target

    print(f"[{country}] matched {len(final_matches)} of {len(channel_blocks)} EPG channels "
          f"(playlist has {len(playlist_index)} unique {country} channels)")

    # Build remapped channel blocks
    channel_out = []
    for block, epg_id in channel_blocks:
        if epg_id in final_matches:
            new_id = final_matches[epg_id]
            channel_out.append(block.replace(f'id="{epg_id}"', f'id="{new_id}"', 1))

    # Build remapped programme blocks
    prog_pattern = re.compile(r'<programme([^>]*?)channel="([^"]+)"([^>]*)>(.*?)</programme>', re.DOTALL)
    prog_out = []
    for m in prog_pattern.finditer(epg_content):
        pre, chan, post, body = m.groups()
        if chan in final_matches:
            new_id = final_matches[chan]
            prog_out.append(f'<programme{pre}channel="{new_id}"{post}>{body}</programme>')

    print(f"[{country}] {len(prog_out)} programme entries carried over")
    return channel_out, prog_out


def main():
    with open("channels_reference.json", "r", encoding="utf-8") as f:
        reference = json.load(f)

    all_channel_blocks = []
    all_programme_blocks = []
    seen_ids = set()

    for country, url in SOURCES.items():
        if country not in reference:
            print(f"[{country}] skipped, not in channels_reference.json")
            continue
        playlist_index = build_playlist_index(reference[country])
        channel_out, prog_out = process_country(country, url, playlist_index)

        for block in channel_out:
            m = re.search(r'<channel id="([^"]+)">', block)
            cid = m.group(1)
            if cid not in seen_ids:
                seen_ids.add(cid)
                all_channel_blocks.append(block)

        all_programme_blocks.extend(prog_out)

    out_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<tv generator-info-name="Auto-remapped EPG (NL/UK/USA)">',
    ]
    out_lines.extend(all_channel_blocks)
    out_lines.extend(all_programme_blocks)
    out_lines.append('</tv>')

    result = "\n".join(out_lines)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(result)

    print(f"\nDone. {len(all_channel_blocks)} total channels, "
          f"{len(all_programme_blocks)} total programmes.")
    print(f"Output written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
