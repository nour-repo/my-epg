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
import difflib
import urllib.request
from collections import defaultdict

# Multiple sources per country, tried in order. Later sources only fill
# gaps left by earlier ones (a channel matched once is never re-matched).
SOURCES = {
    "NL": [
        "https://epgshare01.online/epgshare01/epg_ripper_NL1.xml.gz",
    ],
    "UK": [
        "https://epgshare01.online/epgshare01/epg_ripper_UK1.xml.gz",
    ],
    "USA": [
        "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz",
        "https://epgshare01.online/epgshare01/epg_ripper_US_SPORTS1.xml.gz",
        "https://epgshare01.online/epgshare01/epg_ripper_US_LOCALS1.xml.gz",
    ],
}

OUTPUT_FILE = "combined_epg.xml"
FUZZY_THRESHOLD = 0.88  # 0-1, higher = stricter. Only used as a last resort.


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


def extract_channels(epg_content):
    """Returns list of (block, epg_id, display_name)."""
    channel_blocks = re.findall(r'(<channel id="([^"]+)">.*?</channel>)', epg_content, re.DOTALL)
    out = []
    for block, epg_id in channel_blocks:
        m = re.search(r'<display-name[^>]*>([^<]+)</display-name>', block)
        if m:
            out.append((block, epg_id, m.group(1)))
    return out


def remap_and_extract_programmes(epg_content, id_map):
    """id_map: epg_id -> target tvg_id. Returns list of remapped <programme> blocks."""
    prog_pattern = re.compile(r'<programme([^>]*?)channel="([^"]+)"([^>]*)>(.*?)</programme>', re.DOTALL)
    prog_out = []
    for m in prog_pattern.finditer(epg_content):
        pre, chan, post, body = m.groups()
        if chan in id_map:
            new_id = id_map[chan]
            prog_out.append(f'<programme{pre}channel="{new_id}"{post}>{body}</programme>')
    return prog_out


def process_country(country, urls, playlist_index):
    remaining = dict(playlist_index)  # norm_name -> tvg_id, shrinks as we match
    channel_out = []
    programme_out = []
    seen_targets = set()

    # Pass 1: exact normalized-name match, one source at a time, filling gaps
    for url in urls:
        if not remaining:
            break
        print(f"[{country}] downloading {url}")
        try:
            epg_content = download_epg(url)
        except Exception as e:
            print(f"[{country}] FAILED to download {url}: {e}")
            continue

        channels = extract_channels(epg_content)
        id_map = {}  # epg_id -> target tvg_id, for this source only
        for block, epg_id, disp in channels:
            norm = normalize(disp)
            if norm in remaining:
                target = remaining[norm]
                if target not in seen_targets:
                    id_map[epg_id] = target
                    seen_targets.add(target)
                    channel_out.append(block.replace(f'id="{epg_id}"', f'id="{target}"', 1))
                del remaining[norm]

        matched_this_source = len(id_map)
        programme_out.extend(remap_and_extract_programmes(epg_content, id_map))
        print(f"[{country}] {url.rsplit('/', 1)[-1]}: +{matched_this_source} channels "
              f"({len(remaining)} still unmatched)")

        # Pass 2 (fuzzy fallback): for names that didn't match exactly, try
        # close matches against whatever's left unmatched after this source.
        if remaining:
            fuzzy_id_map = {}
            unmatched_norms = list(remaining.keys())
            for block, epg_id, disp in channels:
                if epg_id in id_map:
                    continue  # already used above
                norm = normalize(disp)
                if not norm:
                    continue
                best = difflib.get_close_matches(norm, unmatched_norms, n=1, cutoff=FUZZY_THRESHOLD)
                if best:
                    target = remaining[best[0]]
                    if target not in seen_targets:
                        fuzzy_id_map[epg_id] = target
                        seen_targets.add(target)
                        channel_out.append(block.replace(f'id="{epg_id}"', f'id="{target}"', 1))
                        del remaining[best[0]]
                        unmatched_norms.remove(best[0])
            if fuzzy_id_map:
                programme_out.extend(remap_and_extract_programmes(epg_content, fuzzy_id_map))
                print(f"[{country}] {url.rsplit('/', 1)[-1]}: +{len(fuzzy_id_map)} more via fuzzy match "
                      f"({len(remaining)} still unmatched)")

        # Pass 3 (substring fallback): catches cases like playlist "WCBS"
        # matching an EPG display name like "WCBS New York (CBS)" — names
        # that contain the target as a chunk but aren't textually similar
        # enough for the fuzzy pass above. Guarded by a minimum length to
        # avoid short/ambiguous names matching too broadly.
        if remaining:
            substring_id_map = {}
            for block, epg_id, disp in channels:
                if epg_id in id_map:
                    continue
                norm_disp = normalize(disp)
                if not norm_disp:
                    continue
                for norm_name, target in list(remaining.items()):
                    if len(norm_name) < 4:
                        continue  # too short, too likely to false-match
                    if target in seen_targets:
                        continue
                    if norm_name in norm_disp:
                        substring_id_map[epg_id] = target
                        seen_targets.add(target)
                        channel_out.append(block.replace(f'id="{epg_id}"', f'id="{target}"', 1))
                        del remaining[norm_name]
                        break
            if substring_id_map:
                programme_out.extend(remap_and_extract_programmes(epg_content, substring_id_map))
                print(f"[{country}] {url.rsplit('/', 1)[-1]}: +{len(substring_id_map)} more via substring match "
                      f"({len(remaining)} still unmatched)")

    total_matched = len(playlist_index) - len(remaining)
    print(f"[{country}] TOTAL: {total_matched} of {len(playlist_index)} playlist channels matched")
    if remaining:
        sample = list(remaining.values())[:15]
        print(f"[{country}] still unmatched (sample of {len(remaining)}): {sample}")

    return channel_out, programme_out


def main():
    with open("channels_reference.json", "r", encoding="utf-8") as f:
        reference = json.load(f)

    all_channel_blocks = []
    all_programme_blocks = []
    seen_ids = set()

    for country, urls in SOURCES.items():
        if country not in reference:
            print(f"[{country}] skipped, not in channels_reference.json")
            continue
        playlist_index = build_playlist_index(reference[country])
        channel_out, prog_out = process_country(country, urls, playlist_index)

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
