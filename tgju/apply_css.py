import re, io

SRC = r'D:\Hermes\TGJU-Telegram\platform\tgju_platform_ui.html'
TMP = r'D:\Hermes\TGJU-Telegram\tgju\new_css_block.txt'

with io.open(SRC, encoding='utf-8') as f:
    src = f.read()
with io.open(TMP, encoding='utf-8') as f:
    new_css = f.read()

# 1) Extract the EXACT original @font-face block (lines 11-16) to preserve byte-for-byte
m = re.search(r'@font-face\{.*?\n\}', src, re.S)
assert m, 'original @font-face not found'
fontface = m.group(0)
assert fontface.startswith('@font-face{') and 'Vazirmatn' in fontface

# 2) New temp file includes the closing </style> at its end — strip it for assembly
new_css = new_css.rstrip()
assert new_css.endswith('</style>')
new_css = new_css[: -len('</style>')].rstrip()

# 3) Insert the preserved font-face right after the reset line
needle = '* { margin:0; padding:0; box-sizing:border-box; }'
assert needle in new_css
new_css = new_css.replace(needle, needle + '\n\n' + fontface, 1)

# 4) Surgical replacement of everything between <style> and its first </style>
style_re = re.compile(r'<style>.*?</style>', re.S)
old_block = style_re.search(src)
assert old_block, 'style block not found'
assert old_block.group(0).count('</style>') == 1, 'multiple </style> in match'

new_block = '<style>\n' + new_css + '\n</style>'
src_out = src[:old_block.start()] + new_block + src[old_block.end():]

with io.open(SRC, 'w', encoding='utf-8', newline='') as f:
    f.write(src_out)

# 5) Verify round-trip integrity
with io.open(SRC, encoding='utf-8') as f:
    check = f.read()
assert check.count('@font-face{') == 1
assert fontface in check, 'font-face lost in write'
# CSS + head untouched
assert check.startswith('<!DOCTYPE html>\n<html lang="fa" dir="rtl">')
assert '</head>' in check and '<body>' in check
# old blue glass tokens gone, new indigo tokens present
assert '#2563eb' not in check, 'old blue accent still present!'
assert '--accent:#4f46e5' in check
assert 'backdrop-filter' not in check, 'glass leftover!'
print('OK: style block replaced, font-face preserved, no glass/blue remnants')