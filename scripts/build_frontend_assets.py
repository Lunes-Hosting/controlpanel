"""Maintainer-only asset build. Generated CSS/fonts/gzip files are committed.

Run with Python (fonttools[woff] and tinycss2) and Node/npm installed. The running
application never invokes this script, downloads tools, or requires Node.
"""

from pathlib import Path
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path(tempfile.gettempdir()) / 'controlpanel-asset-build'
STATIC = ROOT / 'static'


def fetch(url, name):
    destination = CACHE / 'sources' / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        with urlopen(url, timeout=60) as response:
            destination.write_bytes(response.read())
    return destination


def build_utilities():
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Node is required only when rebuilding committed frontend assets')
    modules = CACHE / 'node_modules'
    if not (modules / '@tailwindcss/cli/dist/index.mjs').exists():
        npm = Path(shutil.which('npm') or '').parent / 'node_modules/npm/bin/npm-cli.js'
        subprocess.run([node, str(npm), 'install', '--prefix', str(CACHE), '--no-audit', '--no-fund',
                        '--package-lock=false', 'tailwindcss-v3@npm:tailwindcss@3.4.17',
                        'tailwindcss@4.1.18', '@tailwindcss/cli@4.1.18'], check=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    config = {'content': [(ROOT / 'templates/**/*.html').as_posix(),
                          (STATIC / 'js/**/*.js').as_posix()], 'theme': {'extend': {}}, 'plugins': []}
    (CACHE / 'v3.config.cjs').write_text('module.exports=' + json.dumps(config), encoding='utf-8')
    (CACHE / 'v3.css').write_text('@tailwind base;@tailwind components;@tailwind utilities;', encoding='utf-8')
    subprocess.run([node, str(modules / 'tailwindcss-v3/lib/cli.js'), '-i', str(CACHE / 'v3.css'),
                    '-c', str(CACHE / 'v3.config.cjs'), '-o', str(STATIC / 'css/utilities-v3.css'),
                    '--minify'], check=True, cwd=ROOT)
    (CACHE / 'v4.css').write_text(
        '@import "tailwindcss" source(none);\n' +
        '@source ' + json.dumps((ROOT / 'templates').as_posix()) + ';\n' +
        '@source ' + json.dumps((STATIC / 'js').as_posix()) + ';\n', encoding='utf-8')
    subprocess.run([node, str(modules / '@tailwindcss/cli/dist/index.mjs'), '-i', str(CACHE / 'v4.css'),
                    '-o', str(STATIC / 'css/utilities-v4.css'), '--minify'], check=True, cwd=ROOT)


def subset_font(source, destination, family, style, unicodes):
    from fontTools import subset
    from fontTools.ttLib import TTFont
    font = TTFont(source, recalcTimestamp=False)
    options = subset.Options()
    options.flavor = 'woff2'
    options.name_IDs = ['*']
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=set(unicodes) & set(font.getBestCmap()))
    subsetter.subset(font)
    # Modified subsets get project-specific family names; copyright/license
    # records remain intact, and the upstream license files ship alongside them.
    for record in font['name'].names:
        value = {1: family, 2: style, 3: family + '-' + style,
                 4: family + ' ' + style, 6: family.replace(' ', '') + '-' + style,
                 16: family, 17: style}.get(record.nameID)
        if value is not None:
            record.string = value.encode(record.getEncoding())
    font.flavor = 'woff2'
    font.save(destination)


def build_fonts():
    import tinycss2
    fonts = STATIC / 'fonts'
    fonts.mkdir(exist_ok=True)
    licenses = STATIC / 'licenses'
    licenses.mkdir(exist_ok=True)
    upstream = fetch('https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/css/all.min.css', 'fontawesome.css')
    markup = '\n'.join(path.read_text(encoding='utf-8') for path in (ROOT / 'templates').rglob('*.html'))
    markup += '\n'.join(path.read_text(encoding='utf-8') for path in (STATIC / 'js').glob('*.js'))
    used = set(re.findall(r'\bfa-[a-z0-9-]+', markup))
    rules = tinycss2.parse_stylesheet(upstream.read_text(encoding='utf-8'), skip_whitespace=True, skip_comments=True)
    output = []
    unicodes = set()
    for rule in rules:
        if rule.type == 'at-rule' and rule.lower_at_keyword == 'font-face':
            continue
        glyph = None
        if rule.type == 'qualified-rule':
            for declaration in tinycss2.parse_declaration_list(rule.content):
                if declaration.type == 'declaration' and declaration.name in ('content', '--fa'):
                    strings = [token.value for token in declaration.value if token.type == 'string']
                    if strings and len(strings[0]) == 1:
                        glyph = ord(strings[0])
                        break
        if glyph is not None:
            selectors = set(re.findall(r'\bfa-[a-z0-9-]+', tinycss2.serialize(rule.prelude)))
            if not used.intersection(selectors):
                continue
            unicodes.add(glyph)
        output.append(tinycss2.serialize([rule]))
    css = ''.join(output).replace('Font Awesome 6 Free', 'Lunes Icons').replace('Font Awesome 6 Brands', 'Lunes Brands')
    faces = []
    for filename, family, style, weight in (
        ('fa-solid-900', 'Lunes Icons', 'Solid', 900),
        ('fa-regular-400', 'Lunes Icons', 'Regular', 400),
        ('fa-brands-400', 'Lunes Brands', 'Regular', 400),
    ):
        source = fetch(f'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/webfonts/{filename}.woff2', filename + '.woff2')
        subset_font(source, fonts / (filename + '.woff2'), family, style, unicodes)
        version = hashlib.blake2s((fonts / (filename + '.woff2')).read_bytes(), digest_size=8).hexdigest()
        faces.append(f'@font-face{{font-family:"{family}";font-style:normal;font-weight:{weight};'
                     f'font-display:swap;src:url("../fonts/{filename}.woff2?v={version}") format("woff2")}}')
    (STATIC / 'css/icons.css').write_text('/* Font Awesome Free 6.7.2; see /static/licenses/fontawesome.txt */\n' + ''.join(faces) + css, encoding='utf-8')
    inter = fetch('https://raw.githubusercontent.com/rsms/inter/v4.1/docs/font-files/InterVariable.woff2', 'inter-variable.woff2')
    letters = set(range(0x530)) | set(range(0x1E00, 0x1F00)) | set(range(0x2000, 0x2070)) | set(range(0x20A0, 0x20D0))
    subset_font(inter, fonts / 'lunes-sans.woff2', 'Lunes Sans', 'Regular', letters)
    (STATIC / 'css/interface-font.css').write_text(
        '/* Derived from Inter 4.1; see /static/licenses/inter.txt */\n'
        '@font-face{font-family:"Lunes Sans";font-style:normal;font-weight:100 900;'
        'font-display:optional;src:url("../fonts/lunes-sans.woff2?v=' + hashlib.blake2s((fonts / 'lunes-sans.woff2').read_bytes(), digest_size=8).hexdigest() + '") format("woff2")}', encoding='utf-8')
    shutil.copyfile(fetch('https://raw.githubusercontent.com/FortAwesome/Font-Awesome/6.7.2/LICENSE.txt', 'fontawesome-license.txt'), licenses / 'fontawesome.txt')
    shutil.copyfile(fetch('https://raw.githubusercontent.com/rsms/inter/master/LICENSE.txt', 'inter-license.txt'), licenses / 'inter.txt')
    print(f'Bundled {len(unicodes)} icon codepoints and a local text-font subset')


def bundle_assets():
    for name, parts in {
        'dashboard.css': ['utilities-v3.css', 'interface-font.css', 'icons.css', 'account.css'],
        'panel-ui.css': ['utilities-v4.css', 'interface-font.css', 'icons.css', 'panel.css'],
        'modern-ui.css': ['utilities-v4.css', 'interface-font.css', 'icons.css'],
        'legacy-ui.css': ['utilities-v3.css', 'interface-font.css', 'icons.css'],
    }.items():
        (STATIC / 'css' / name).write_text('\n'.join((STATIC / 'css' / part).read_text(encoding='utf-8') for part in parts), encoding='utf-8')


def compress_assets():
    for path in STATIC.rglob('*'):
        if path.suffix in ('.css', '.js', '.svg') and path.stat().st_size >= 1024:
            path.with_name(path.name + '.gz').write_bytes(gzip.compress(path.read_bytes(), compresslevel=9, mtime=0))


if __name__ == '__main__':
    build_utilities()
    build_fonts()
    bundle_assets()
    compress_assets()
