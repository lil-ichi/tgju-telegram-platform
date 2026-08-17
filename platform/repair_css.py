import re

SRC = r'D:\Hermes\TGJU-Telegram\platform\tgju_platform_ui.html'

chk = open(SRC, encoding='utf-8').read()

# ── Font-face repair: remove duplicate/truncated @font-face blocks ──────────
# A valid @font-face block must have properly closed src:url(...).
# The CSS spec requires the ENTIRE <style> block to be valid — a single
# unclosed url() parenthesis will cause the browser to reject ALL CSS in
# the file, rendering the page as plain HTML. This catches both the
# literal '[truncated]' marker (from file-read truncation) and the silent
# case where base64 data just ends mid-string without a closing parenthesis.
ff_starts = [m.start() for m in re.finditer(r'@font-face\{', chk)]
print('font-face blocks found:', len(ff_starts))
for st in ff_starts:
    end = chk.find('\n}', st)
    assert end != -1
    block = chk[st:end + 2]
    has_truncated_marker = '[truncated]' in block
    url_open = block.count('url(')
    url_close = block.count(')')
    has_format = "format('woff2')" in block
    # url_open should equal url_close minus the ) from format('woff2')
    # If url_open > url_close, the url() is not properly closed → BROKEN
    is_broken = has_truncated_marker or not has_format or (url_open > url_close)
    if is_broken:
        print('removing broken font-face block at', st, 'len', len(block),
              '(truncated_marker={}, has_format={}, url_open={}, url_close={})'.format(
                  has_truncated_marker, has_format, url_open, url_close))
        chk = chk[:st] + chk[end + 2:]

# ── Integrity asserts ────────────────────────────────────────────────────────
assert chk.count('@font-face{') == 1, chk.count('@font-face{')
assert chk.count('[truncated]') == 0
assert chk.count("format('woff2')") == 1
m = re.search(r'@font-face\{.*?\n\}', chk, re.S)
ff = m.group(0)
assert ff.startswith("@font-face{\n  font-family:'Vazirmatn';")
assert 'd09GMgABAAAAAbIwABQAAAADsWQAAbG6' in ff          # original base64 prefix
assert ff.rstrip().endswith("font-display:swap;}")
assert len(ff) > 100000, len(ff)                          # full 148KB blob intact
assert chk.startswith('<!DOCTYPE html>\n<html lang="fa" dir="rtl">')
assert chk.count('<style>') == 1 and chk.count('</style>') == 1
assert chk.count('</html>') == 1 and chk.count('</body>') == 1
assert 'backdrop-filter' not in chk and '-webkit-backdrop-filter' not in chk
assert '#2563eb' not in chk and '#1d4ed8' not in chk and '#3b82f6' not in chk
assert '--accent:#4f46e5' in chk and 'rgba(79,70,229,.12)' in chk
assert 'TGJU پلتفرم' in chk                                  # body intact
assert 'function loadChannelsData' in chk or 'loadChannelsData' in chk

open(SRC, 'w', encoding='utf-8', newline='').write(chk)
print('REPAIRED. final size:', len(chk))
