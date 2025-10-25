"""
Generate search algorithm testing synthetic dataset (S1). This may be iterated on in the future.
Summary is not included in search testing.

Dataset constraints:
1. 1000 archives (1-5 pages each)
    a. at least 10 archives with same title
    b. at least 10 archives with no title
    c. at least 10 archives with more than 10 tags
2. 20 untagged archives
3. 1000 distinct non-namespaced tags (randomized characters)
    a. at least 50 tags with a space
    b. at least 50 tags with a special ascii character (i.e. non alpha-numeric character)
    c. at least 50 tags with a special unicode character (i.e. non ascii character)
    d. no tags use reserved characters (see search filter)
4. 8 tag namespaces with unstructured values (artist, source, etc.)
    a. at least 1 namespaced tag with at least 500 archives
    b. at least 1 namespaced tag with 10 duplicate values
    c. at least 4 namespaces have special unicode characters
    d. "artist" namespace exists with following property:
        1. 300 distinct artists.
        2. 600 archives have exactly 1 artist in their tags.
        3. 20 archives have 2 different artists in their tags (i.e. "artist:artist-1",artist:artist-2")
        4. 10 archives have 10 different artists in their tags.
        5. At least 100 archives have 1 common artist.
        6. At least 20 archives have 1 common artist, for 10 artists.
    e. "source" namespace exists with the property that all values are ascii only.
5. 2 tag namespaces with epoch second values (1445644800 - 1761366700)
    a. at least 1 namespaced tag with at least 500 archives
    b. at least 1 namespaced tag with 10 duplicate values
6. at least one non-namespaced tag has at least 200 archives.

7. 1 static category with 200 archives
8. 1 static category with 0 archives
9. 1 dynamic category with namespace
10. 1 dynamic category with a normal tag
11. at least 1 dynamic category for each special character (quotation marks, question mark, underscore, asterisk, percentage sign, subtraction sign, dollar sign)

12. at least 10 archives has a very long title (200 characters or more)
13. at least 10 tags are very long (100 characters or more)
14. at least 15 archives have 200 tags or more

Recommendations:
- all unstructured data is fuzzily generated with unicode.
"""

import argparse
import json
import logging
from pathlib import Path
import random
import string
from typing import Dict, List, Set

from aio_lanraragi_tests.search_algorithm_testing.models import S1ArchiveInfo, S1CategoryInfo

LOGGER = logging.getLogger(__name__)

# reserved characters (not allowed in individual tags, but allow special behavior for search filtering)
RESERVED_CHARS = ["\"", "?", "_", "*", "%", "-", "$", ","]

def generate_dataset(seed: int):
    """
    Generate archives and categories satisfying validate_s1 constraints.

    Only seed 42 is tested to pass validation.
    Returns: (archives, categories)
    """
    random.seed(seed)

    num_archives = 1000
    all_preids = list(range(num_archives))

    # Titles and pages
    duplicate_title = "Duplicate Title"
    empty_title_count = 10
    duplicate_title_count = 10

    empty_title_preids = set(random.sample(all_preids, empty_title_count))
    remaining_for_dupes = [i for i in all_preids if i not in empty_title_preids]
    duplicate_title_preids = set(random.sample(remaining_for_dupes, duplicate_title_count))

    def fuzz_word(min_len: int = 3, max_len: int = 12) -> str:
        alphabet = string.ascii_letters + string.digits
        return "".join(random.choices(alphabet, k=random.randint(min_len, max_len)))

    def fuzz_title_base() -> str:
        # variable number of words with mixed lengths
        words: List[str] = []
        for _ in range(random.randint(2, 8)):
            w = fuzz_word(2, random.randint(4, 14))
            # randomly capitalize
            if random.random() < 0.3:
                w = w.capitalize()
            # occasionally inject unicode
            if random.random() < 0.25:
                u = random.choice(["é", "ü", "ñ", "ß", "ø", "π", "δ", "猫", "雪", "Ω", "α", "β", "漢", "🌟"])
                pos = random.randint(0, len(w))
                w = w[:pos] + u + w[pos:]
            words.append(w)
        return " ".join(words)

    def long_title() -> str:
        # build a long title >= 200 chars with varied words
        s = []
        while len(" ".join(s)) < 210:
            # include some numbers, some long words
            part = fuzz_word(4, random.randint(8, 24))
            if random.random() < 0.2:
                u = random.choice(["é", "ü", "ñ", "ß", "ø", "π", "δ", "猫", "雪", "Ω", "α", "β", "漢", "🌟"])
                pos = random.randint(0, len(part))
                part = part[:pos] + u + part[pos:]
            s.append(part)
        return " ".join(s)[: max(200, len(" ".join(s)))]

    titles: Dict[int, str] = {}
    # choose long title archives ensuring we don't pick empty title ones
    long_title_candidates = [pid for pid in all_preids if pid not in empty_title_preids]
    long_title_preids = set(random.sample(long_title_candidates, 12))

    for pid in all_preids:
        if pid in empty_title_preids:
            titles[pid] = ""
        elif pid in duplicate_title_preids:
            titles[pid] = duplicate_title
        elif pid in long_title_preids:
            titles[pid] = long_title()
        else:
            titles[pid] = fuzz_title_base()

    pages: Dict[int, int] = {pid: random.randint(1, 5) for pid in all_preids}

    # Build exactly 1000 distinct non-namespaced tags
    def is_tag_valid(tag: str) -> bool:
        if not tag:
            return False
        if ":" in tag:
            return False
        return all(rc not in tag for rc in RESERVED_CHARS)

    def rand_ascii_special() -> str:
        # allowed ascii specials, excluding reserved and ':'
        allowed = "!.;'/()+==~^&#@."
        # ensure no comma or percent or underscore etc.
        allowed = "".join(ch for ch in allowed if ch not in RESERVED_CHARS and ch != ":")
        return random.choice(allowed)

    unicode_pool = [
        "é", "ü", "ñ", "ß", "ø", "π", "δ", "猫", "雪", "東京", "Ω", "α", "β", "漢", "🌟", "💫",
    ]

    def make_space_tag() -> str:
        parts = []
        for _ in range(random.randint(2, 5)):
            parts.append(fuzz_word(2, random.randint(4, 16)))
        tag = " ".join(parts)
        if is_tag_valid(tag):
            return tag
        return make_space_tag()

    def make_ascii_tag() -> str:
        base = fuzz_word(3, random.randint(6, 18))
        # insert 1-3 ascii specials
        for _ in range(random.randint(1, 3)):
            ch = rand_ascii_special()
            pos = random.randint(1, len(base) - 1)
            base = base[:pos] + ch + base[pos:]
        return base if is_tag_valid(base) else make_ascii_tag()

    def make_unicode_tag() -> str:
        base = fuzz_word(3, random.randint(6, 18))
        uni = random.choice(unicode_pool)
        pos = random.randint(1, len(base) - 1)
        tag = base[:pos] + uni + base[pos:]
        return tag if is_tag_valid(tag) else make_unicode_tag()

    def make_long_tag() -> str:
        # generate long tag 100-140 chars with spaces/some ascii/unicode occasionally
        pieces: List[str] = []
        while len("".join(pieces)) < 105:
            choice = random.random()
            if choice < 0.6:
                pieces.append(fuzz_word(3, random.randint(6, 16)))
            elif choice < 0.85:
                # add space to vary
                pieces.append(" ")
            elif choice < 0.95:
                pieces.append(rand_ascii_special())
            else:
                pieces.append(random.choice(unicode_pool))
        tag = "".join(pieces)[: random.randint(100, 140)]
        # strip leading/trailing spaces
        tag = tag.strip()
        # avoid empty
        if not tag:
            return make_long_tag()
        return tag if is_tag_valid(tag) else make_long_tag()

    non_ns_tags: List[str] = []
    tag_set: Set[str] = set()

    # Ensure required counts
    def add_unique(tag: str):
        if tag not in tag_set and is_tag_valid(tag):
            non_ns_tags.append(tag)
            tag_set.add(tag)

    # Preload special categories
    for _ in range(60):
        add_unique(make_space_tag())
    for _ in range(60):
        add_unique(make_ascii_tag())
    for _ in range(60):
        add_unique(make_unicode_tag())
    for _ in range(15):
        add_unique(make_long_tag())

    # Also include a deterministic popular tag
    add_unique("popular.tag")

    # Fill remaining with varied patterns to reach 1000
    allowed_chars = string.ascii_lowercase + string.digits
    while len(non_ns_tags) < 1000:
        mode = random.random()
        if mode < 0.25:
            candidate = make_space_tag()
        elif mode < 0.5:
            candidate = make_ascii_tag()
        elif mode < 0.65:
            candidate = make_unicode_tag()
        else:
            base = "".join(random.choices(allowed_chars, k=random.randint(5, 14)))
            if random.random() < 0.35:
                insert_pos = random.randint(1, max(1, len(base) - 1))
                candidate = base[:insert_pos] + "." + base[insert_pos:]
            else:
                candidate = base
        add_unique(candidate)

    # Choose 20 untagged archives
    untagged_count = 20
    untagged_preids = set(random.sample(all_preids, untagged_count))

    # Assign at least one unique non-ns tag to every tagged archive so all 1000 appear
    non_untagged_preids = [i for i in all_preids if i not in untagged_preids]
    tag_assignment: Dict[int, Set[str]] = {pid: set() for pid in non_untagged_preids}
    for idx, tag in enumerate(non_ns_tags):
        pid = non_untagged_preids[idx % len(non_untagged_preids)]
        tag_assignment[pid].add(tag)

    # Spread additional random non-ns tags per archive
    for pid in non_untagged_preids:
        existing = len(tag_assignment[pid])
        target = random.randint(3, 12)
        if target > existing:
            needed = target - existing
            candidates = [t for t in non_ns_tags if t not in tag_assignment[pid]]
            chosen = random.sample(candidates, min(needed, len(candidates)))
            tag_assignment[pid].update(chosen)

    # Ensure at least 10 archives have >= 10 total tags
    high_tag_preids = list(non_untagged_preids[:20])
    for pid in high_tag_preids:
        while len(tag_assignment[pid]) < 12:
            candidate = random.choice(non_ns_tags)
            tag_assignment[pid].add(candidate)

    # Ensure one tag appears in >= 200 archives
    popular_tag = "popular.tag"
    popular_targets = set(non_untagged_preids[:250])
    for pid in popular_targets:
        tag_assignment[pid].add(popular_tag)

    # Ensure at least 15 archives have >= 200 tags
    heavy_preids = set(random.sample(non_untagged_preids, 15))
    for pid in heavy_preids:
        while len(tag_assignment[pid]) < 210:
            tag_assignment[pid].add(random.choice(non_ns_tags))

    # Namespaced tags
    # define unstructured namespaces list (excluding structured)
    unstructured_namespaces = [
        "artist",
        "circle",
        "parody",
        "character",
        "language",
        "source",
        "rating",
        "misc",
    ]
    other_unstructured = [ns for ns in unstructured_namespaces if ns != "artist"]

    EPOCH_MIN = 1445644800
    EPOCH_MAX = 1761366700

    # Deterministic lists for structured namespace assignment
    uploaded_list = non_untagged_preids[:700]
    published_list = non_untagged_preids[700:1300]
    published_list = {pid for pid in published_list if pid in non_untagged_preids}

    uploaded_common_value = str(1609459200)
    published_common_value = str(1577836800)
    def random_name() -> str:
        parts = []
        for _ in range(random.randint(1, 3)):
            wl = random.randint(3, 12)
            parts.append("".join(random.choices(string.ascii_letters, k=wl)).capitalize())
        return " ".join(parts)

    def random_unicode_name() -> str:
        base = random_name()
        # inject one or two unicode codepoints
        for _ in range(random.randint(1, 2)):
            uni = random.choice(unicode_pool)
            pos = random.randint(0, len(base))
            base = base[:pos] + uni + base[pos:]
        return base

    # Build artist name pool (>=320 distinct), mixing unicode and ascii
    artist_common_value = random_name()
    artist_name_set: Set[str] = set()
    artist_name_set.add(artist_common_value)
    while len(artist_name_set) < 320:
        if random.random() < 0.5:
            artist_name_set.add(random_name())
        else:
            # ensure reserved characters are not used
            candidate = random_unicode_name()
            if all(rc not in candidate for rc in RESERVED_CHARS):
                artist_name_set.add(candidate)
    # Sort for deterministic base ordering, then shuffle deterministically
    artist_names: List[str] = sorted(artist_name_set)
    random.shuffle(artist_names)

    def add_tag(pid: int, tag: str):
        if any(rc in tag for rc in RESERVED_CHARS):
            return
        tag_assignment[pid].add(tag)

    # Structured namespaces with guaranteed duplicates
    for i, pid in enumerate(uploaded_list):
        if i < 12:
            val = uploaded_common_value
        else:
            val = str(random.randint(EPOCH_MIN, EPOCH_MAX))
        add_tag(pid, f"uploaded:{val}")

    for i, pid in enumerate(published_list):
        if i < 12:
            val = published_common_value
        else:
            val = str(random.randint(EPOCH_MIN, EPOCH_MAX))
        add_tag(pid, f"published:{val}")

    # Assign artists to satisfy 4d:
    # - 600 archives with exactly 1 artist
    # - 20 archives with exactly 2 artists
    # - 10 archives with exactly 10 artists
    # - >=300 distinct artists overall
    # - One artist appears in >=100 archives, and at least 10 artists appear in >=20

    available_pids = list(non_untagged_preids)
    random.shuffle(available_pids)
    ten_artist_pids = available_pids[:10]
    two_artist_pids = available_pids[10:30]
    single_list = available_pids[30:630]  # 600 pids

    # pick popular artists deterministically from artist_names
    top_artist = artist_common_value
    popular_artists = [a for a in artist_names if a != top_artist][:9]

    # 120 singles get the top artist
    for pid in single_list[:120]:
        add_tag(pid, f"artist:{top_artist}")

    # Next 9 artists get 20 each
    idx_offset = 120
    for popular in popular_artists:
        targets = single_list[idx_offset:idx_offset + 20]
        for pid in targets:
            add_tag(pid, f"artist:{popular}")
        idx_offset += 20

    # Remaining singles get unique artists to reach >=300 distinct
    remaining_singles = single_list[idx_offset:]
    unique_artist_pool = [a for a in artist_names if a not in set([top_artist] + popular_artists)]
    # ensure at least 300 unique singles
    for pid, artist in zip(remaining_singles, unique_artist_pool):
        add_tag(pid, f"artist:{artist}")

    # Exactly 2 artists for 20 archives
    tail_pool = unique_artist_pool[300:]
    if len(tail_pool) < 100:
        tail_pool = unique_artist_pool
    for pid in two_artist_pids:
        choices = random.sample(tail_pool, 2)
        for artist in choices:
            add_tag(pid, f"artist:{artist}")

    # Exactly 10 artists for 10 archives
    for pid in ten_artist_pids:
        choices = random.sample(tail_pool, 10)
        for artist in choices:
            add_tag(pid, f"artist:{artist}")

    # Ensure all unstructured namespaces appear at least once
    for i, ns in enumerate(other_unstructured):
        pid = non_untagged_preids[(300 + i) % len(non_untagged_preids)]
        val_seed = "".join(random.choices(string.ascii_letters, k=6)).capitalize()
        add_tag(pid, f"{ns}:{val_seed}")

    # Randomly add more unstructured namespaces
    for pid in non_untagged_preids:
        count_other = random.randint(0, 3)
        chosen_ns = random.sample(other_unstructured, count_other)
        for ns in chosen_ns:
            r = random.random()
            if ns == "source":
                # ASCII-only for source
                val = random_name()
            else:
                if r < 0.25:
                    val = random_unicode_name()
                elif r < 0.6:
                    val = random_name()
                else:
                    val = "".join(random.choices(string.ascii_letters, k=random.randint(4, 10))).capitalize()
            add_tag(pid, f"{ns}:{val}")

    # Render archives
    archives: List[S1ArchiveInfo] = []
    for pid in all_preids:
        if pid in untagged_preids:
            tags_str = ""
        else:
            tags_str = ",".join(sorted(tag_assignment[pid]))
        archives.append(
            S1ArchiveInfo(
                preid=pid,
                title=titles[pid],
                tags=tags_str,
                pages=pages[pid],
            )
        )

    # Categories
    categories: List[S1CategoryInfo] = []
    cat_preid = 0

    static_200_ids = [str(pid) for pid in non_untagged_preids[:200]]
    categories.append(S1CategoryInfo(preid=cat_preid, name="Static 200", filter=None, archives=static_200_ids))
    cat_preid += 1

    categories.append(S1CategoryInfo(preid=cat_preid, name="Static 0", filter=None, archives=[]))
    cat_preid += 1

    categories.append(S1CategoryInfo(preid=cat_preid, name="Dyn Namespaced", filter=f"artist:{artist_common_value}", archives=None))
    cat_preid += 1

    categories.append(S1CategoryInfo(preid=cat_preid, name="Dyn Non-Namespaced", filter=popular_tag, archives=None))
    cat_preid += 1

    special_filters = {
        "\"": 'has "quotes" inside',
        "?": "contains?question",
        "_": "contains_under_score",
        "*": "star*tag",
        "%": "percent%tag",
        "-": "minus-tag",
        "$": "dollar$tag",
        ",": "comma,tag",
    }
    for ch in RESERVED_CHARS:
        filt = special_filters[ch]
        categories.append(S1CategoryInfo(preid=cat_preid, name=f"Dyn Special {ch}", filter=filt, archives=None))
        cat_preid += 1

    return archives, categories

def validate_dataset(archives: List[S1ArchiveInfo], categories: List[S1CategoryInfo]):
    """
    Validate that data generation constraints are all satisfied, otherwise throw error.
    """

    # constraint 1
    LOGGER.info("Testing constraint 1...")
    assert len(archives) == 1000
    title_frequency: Dict[str, int] = {}
    has_10_arcs_same_title = False
    has_10_arcs_no_title = False
    num_arcs_with_10_or_more_tags = 0
    for a in archives:
        if a.title not in title_frequency:
            title_frequency[a.title] = 0
        title_frequency[a.title] += 1
        if title_frequency[a.title] >= 10:
            if a.title:
                has_10_arcs_same_title = True
            else:
                has_10_arcs_no_title = True
        if len(a.tags.split(",")) >= 10:
            num_arcs_with_10_or_more_tags += 1
    assert has_10_arcs_no_title, "Not found: at least 10 archives with no title"
    assert has_10_arcs_same_title, "Not found: at least 10 archives with same title"
    assert num_arcs_with_10_or_more_tags >= 10, "Number of archives with at least 10 tags less than 10"
    LOGGER.info("Constraint passed.")

    # constraint 2
    LOGGER.info("Testing constraint 2...")
    num_untagged_archives = 0
    for a in archives:
        if a.tags == "":
            num_untagged_archives += 1
    assert num_untagged_archives == 20, "Number of untagged archives not 20."
    LOGGER.info("Constraint passed.")

    # constraint 3-6
    LOGGER.info("Testing constraints 3-6...")
    tagset: Set[str] = set() # non-namespaced tags.
    namespace_value_map: Dict[str, Dict[str, int]] = {} # namespace -> value -> frequency
    tag_archive_frequency: Dict[str, int] = {} # number of archives by tag
    for a in archives:
        tag_list = [t.strip() for t in a.tags.split(",")]
        for tag in tag_list:
            if not tag:
                continue
            assert all(rc not in tag for rc in RESERVED_CHARS), f"Detected reserved character in tag: \"{tag}\""
            if ":" not in tag:
                tagset.add(tag)
                if tag not in tag_archive_frequency:
                    tag_archive_frequency[tag] = 0
                tag_archive_frequency[tag] += 1
            else:
                ns, val = tag.split(":", 1)
                if ns not in namespace_value_map:
                    namespace_value_map[ns] = {}
                if val not in namespace_value_map[ns]:
                    namespace_value_map[ns][val] = 0
                namespace_value_map[ns][val] += 1
    assert len(tagset) == 1000, "Number of tags is not 1000."
    num_tags_with_space = sum(1 for tag in tagset if " " in tag)
    num_tags_with_ascii = sum(1 for tag in tagset if any((not ch.isalnum()) and ch != " " and ord(ch) < 128 for ch in tag))
    num_tags_with_unicode = sum(1 for tag in tagset if not tag.isascii())
    num_very_long_tags = sum(1 for tag in tagset if len(tag) >= 100)
    assert num_tags_with_space >= 50, f"Found {num_tags_with_space} tags with space; need at least 50."
    assert num_tags_with_ascii >= 50, f"Found {num_tags_with_ascii} tags with ascii specials; need at least 50."
    assert num_tags_with_unicode >= 50, f"Found {num_tags_with_unicode} tags with unicode; need at least 50."
    assert num_very_long_tags >= 10, f"Found {num_very_long_tags} very long non-namespaced tags; need at least 10."

    # 4c: at least 4 namespaces with unicode values
    unicode_ns_count = 0
    for ns, values in namespace_value_map.items():
        if any(not val.isascii() for val in values):
            unicode_ns_count += 1
    assert unicode_ns_count >= 4, f"Namespaces with unicode values: {unicode_ns_count}; need at least 4."

    # 4e: source namespace exists and all values are ASCII-only
    assert "source" in namespace_value_map, "'source' namespace not found."
    assert all(val.isascii() for val in namespace_value_map["source"]), "Found non-ASCII value(s) in 'source' namespace."

    num_unstructured_namespaces = 0
    num_structured_namespaces = 0
    has_unstructured_ns_500_archives = False
    has_unstructured_ns_10_dupes = False
    has_structured_ns_500_archives = False
    has_structured_ns_10_dupes = False
    for ns in namespace_value_map:
        values = namespace_value_map[ns]
        total = sum(values[key] for key in values)
        is_structured = True
        has_10_dupes = False
        for k in values:
            if values[k] >= 10:
                has_10_dupes = True
        for value in values:
            if not value.isdigit() or not (1445644800 <= int(value) <= 1761366700):
                is_structured = False
        if is_structured:
            if total >= 500:
                has_structured_ns_500_archives = True
            if has_10_dupes:
                has_structured_ns_10_dupes = True
            num_structured_namespaces += 1
        else:
            if total >= 500:
                has_unstructured_ns_500_archives = True
            if has_10_dupes:
                has_unstructured_ns_10_dupes = True
            num_unstructured_namespaces += 1
    assert num_unstructured_namespaces == 8, "Number of unstructured namespaces not satisfied."
    assert num_structured_namespaces == 2, "Number of epoch second-valued namespaces not satisfied."
    assert has_structured_ns_500_archives, "No structured namespaced tags with 500 archives or more."
    assert has_unstructured_ns_500_archives, "No unstructured namespaced tags with 500 archives or more."
    assert has_structured_ns_10_dupes, "No structured namespace with 10 dupes or more."
    assert has_unstructured_ns_10_dupes, "No unstructured namespace with 10 dupes or more."

    has_tag_over_200_archives = False
    for tag in tag_archive_frequency:
        if tag_archive_frequency[tag] >= 200:
            has_tag_over_200_archives = True
    assert has_tag_over_200_archives, "No tag which belongs to at least 200 archives."
    LOGGER.info("Constraints passed.")

    # constraint 4d (artist namespace specifics)
    LOGGER.info("Testing constraint 4d (artist namespace)...")
    artist_values_map = namespace_value_map.get("artist", {})
    num_distinct_artists = len(artist_values_map)
    assert num_distinct_artists >= 300, f"Found {num_distinct_artists} distinct artists; need at least 300."

    # Per-archive artist counts
    exactly_1_artist = 0
    exactly_2_artists = 0
    exactly_10_artists = 0
    for a in archives:
        if not a.tags:
            continue
        tags = [t.strip() for t in a.tags.split(",") if t.strip()]
        artist_vals = set()
        for t in tags:
            if t.startswith("artist:"):
                artist_vals.add(t.split(":", 1)[1])
        count = len(artist_vals)
        if count == 1:
            exactly_1_artist += 1
        elif count == 2:
            exactly_2_artists += 1
        elif count == 10:
            exactly_10_artists += 1

    assert exactly_1_artist == 600, f"Archives with exactly 1 artist: {exactly_1_artist}; need 600."
    assert exactly_2_artists == 20, f"Archives with exactly 2 artists: {exactly_2_artists}; need 20."
    assert exactly_10_artists == 10, f"Archives with exactly 10 artists: {exactly_10_artists}; need 10."

    # At least one artist occurs in >= 100 archives
    has_artist_100 = any(freq >= 100 for freq in artist_values_map.values())
    assert has_artist_100, "No artist appears in at least 100 archives."

    # At least 10 artists appear in >= 20 archives each
    artists_ge_20 = sum(1 for freq in artist_values_map.values() if freq >= 20)
    assert artists_ge_20 >= 10, f"Artists with >=20 archives: {artists_ge_20}; need at least 10."
    LOGGER.info("Constraints passed.")

    LOGGER.info("Testing constraint 7-11...")
    has_static_200_archives = False
    has_static_0_archives = False
    has_dynamic_namespaced = False
    has_dynamic_non_namespaced = False
    special_chars_presence: Dict[str, bool] = dict.fromkeys(RESERVED_CHARS, False)
    for c in categories:
        assert not (c.archives and c.filter), "Category cannot have both archives and filter set."
        if c.archives is not None:
            if len(c.archives) == 200:
                has_static_200_archives = True
            if len(c.archives) == 0:
                has_static_0_archives = True
        if c.filter:
            if ":" in c.filter:
                has_dynamic_namespaced = True
            else:
                has_dynamic_non_namespaced = True

            for ch in special_chars_presence:
                if ch in c.filter:
                    special_chars_presence[ch] = True

    assert has_static_200_archives, "No static category with exactly 200 archives."
    assert has_static_0_archives, "No static category with exactly 0 archives."
    assert has_dynamic_namespaced, "No dynamic category with a namespaced filter."
    assert has_dynamic_non_namespaced, "No dynamic category with a non-namespaced tag filter."
    for ch, present in special_chars_presence.items():
        assert present, f"No dynamic category found for special character '{ch}'."
    LOGGER.info("Constraints passed.")

    LOGGER.info("Testing constraints 12-14...")
    long_title_count = sum(1 for a in archives if len(a.title) >= 200)
    assert long_title_count >= 10, f"Found {long_title_count} archives with very long titles; need at least 10."

    archives_with_200_or_more_tags = 0
    for a in archives:
        tag_list = [t for t in a.tags.split(",") if t]
        if len(tag_list) >= 200:
            archives_with_200_or_more_tags += 1
    assert archives_with_200_or_more_tags >= 15, f"Found {archives_with_200_or_more_tags} archives with >=200 tags; need at least 15."
    LOGGER.info("Constraints passed.")

def dump_dataset(archives: List[S1ArchiveInfo], categories: List[S1CategoryInfo]) -> str:
    """
    Dump data into a JSON.
    """
    data = {
        "archives": [a.model_dump() for a in archives],
        "categories": [c.model_dump() for c in categories]
    }
    return json.dumps(data, ensure_ascii=False, indent=4)

if __name__ == "__main__":
    """
    Output the generated dataset to disk for analysis.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="Path to dataset output.")
    parser.add_argument("--seed", type=int, default=42, help="Custom seed for random generation.")
    args = parser.parse_args()

    out_file = Path(args.out)
    seed = args.seed

    archives, categories = generate_dataset(seed)

    try:
        validate_dataset(archives, categories)
    except AssertionError:
        print("Validation failed.")

    content = dump_dataset(archives, categories)
    with open(out_file, 'w', encoding='utf-8') as writer:
        writer.write(content)
