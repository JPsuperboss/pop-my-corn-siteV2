"""Synchronise le catalogue du site avec l'Atelier Mosaïque.

Entrée : un dossier de designs exportés de l'Atelier (un JSON par design, comme les écrit
ArtifactData avec out_dir) et le JSON de la palette (document palette/default).
Sortie : site/products.json, netlify/functions/products-data.mts, site/sitemap.xml et
site/images/products/<id>.jpg (rendu 3D, refait seulement pour les designs modifiés).

Usage : python3 tools/atelier-sync/sync.py <dossier designs> <palette.json> [--dry-run]
"""
import json, glob, os, re, sys, hashlib, unicodedata, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
STATE = os.path.join(HERE, 'state.json')
IMG_DIR = os.path.join(REPO, 'site', 'images', 'products')
SITE_URL = 'https://popmycorn.netlify.app'

CAT = {'Animaux': 'Animaux', 'Silhouette chat': 'Animaux', 'Bretagne': 'Régions', 'Mer': 'Extérieur',
       'Baigneurs': 'Extérieur', 'Cœur': 'Maison', 'Poésie': 'Maison', 'Fête': 'Maison', 'Nourriture': 'Maison',
       'Sport': 'Sports', 'Loisirs': 'Autres', 'Sans dossier': 'Autres', 'Collab FCL': 'Collabs'}
CATEGORIES = ['Régions', 'Maison', 'Extérieur', 'Animaux', 'Sports', 'Collabs', 'Métiers', 'Autres']
# palier automatique selon le nombre de carreaux, si le design n'a pas de prix dans l'Atelier
TIERS = [(250, 1990), (400, 2490), (630, 3490), (900, 4490), (10**9, 5490)]
STARS = {1990: 1, 2490: 2, 3490: 3, 4490: 4, 5490: 5}
HERO = ['phares', 'burger', 'chat-noir-assis']  # images utilisées par la page d'accueil

def slug(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')

def load_docs(folder):
    docs = []
    for f in sorted(glob.glob(os.path.join(folder, '*.json'))):
        d = json.load(open(f))
        d = d.get('data', d) if 'cells' not in d else d
        d['_id'] = os.path.splitext(os.path.basename(f))[0]
        docs.append(d)
    return docs

def design_hash(d, palette):
    used = sorted(set(c for c in d['cells'] if c))
    key = json.dumps([d['gridW'], d['gridH'], d['cells'], [palette.get(u) for u in used]])
    return hashlib.sha1(key.encode()).hexdigest()[:16]

def cells_key(d):
    """Empreinte du dessin, identique à cellsKey() de l'Atelier (FNV-1a 32 bits) : dit si le rendu photo est à jour."""
    s = json.dumps([d['gridW'], d['gridH'], d['cells']], separators=(',', ':'))
    h = 0x811c9dc5
    for ch in s:
        h = ((h ^ ord(ch))*0x01000193) & 0xffffffff
    return '%08x' % h

def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    dry = '--dry-run' in sys.argv
    docs = load_docs(args[0])
    pal = json.load(open(args[1]))
    pal = pal.get('data', pal)
    palette = {c['id']: c['hex'] for c in pal['colors']}
    state = json.load(open(STATE)) if os.path.exists(STATE) else {'ids': {}, 'hashes': {}}

    products, skipped, to_render, to_atelier = [], [], [], []
    used_ids = set()
    docs.sort(key=lambda d: d['_id'] not in state['ids'])
    for d in docs:
        if d.get('onSite') is not True:
            continue
        name = (d.get('name') or '').strip()
        cells = d.get('cells') or []
        w = d.get('gridW') or 0
        idx = [i for i, c in enumerate(cells) if c]
        if not name or not idx or not w:
            skipped.append((name or d['_id'], 'design vide ou sans nom')); continue
        unknown = sorted(set(cells[i] for i in idx) - set(palette))
        if unknown:
            skipped.append((name, 'couleur absente de la palette : ' + ', '.join(unknown))); continue
        # identifiant stable : on garde celui déjà attribué à ce design, même s'il est renommé
        pid = state['ids'].get(d['_id'])
        if not pid:
            pid = slug(name)
            taken = used_ids | {v for k, v in state['ids'].items() if k != d['_id']}
            while pid in taken:
                pid += '-2'
        used_ids.add(pid); state['ids'][d['_id']] = pid
        xs = [i % w for i in idx]; ys = [i//w for i in idx]
        cw, ch = max(xs)-min(xs)+1, max(ys)-min(ys)+1
        filled = len(idx); ncol = len(set(cells[i] for i in idx))
        price = int(round(float(d['price'])*100)) if d.get('price') else next(p for lim, p in TIERS if filled <= lim)
        if max(cw, ch) > 41 and not d.get('price'):
            price = 5490
        folder = (d.get('folder') or 'Sans dossier').replace(' :', '').strip().replace('Fete', 'Fête')
        category = d.get('category') if d.get('category') in CATEGORIES else CAT.get(folder, 'Autres')
        tile = 1.2
        desc = (d.get('description') or '').strip() or (
            f"{name} en mosaïque pixel art : {filled} carreaux de verre en {ncol} couleur{'s' if ncol > 1 else ''}, "
            f"pour un motif d'environ {cw*tile:.0f} × {ch*tile:.0f} cm une fois posé.")
        products.append(dict(id=pid, name=name, theme=('Divers' if folder == 'Sans dossier' else folder),
                             category=category, stars=STARS.get(price, 3), price=price, gridW=cw, gridH=ch,
                             tileSizeCm=tile, image=f'images/products/{pid}.jpg', artist=None, description=desc))
        hsh = design_hash(d, palette)
        if state['hashes'].get(pid) != hsh or not os.path.exists(os.path.join(IMG_DIR, pid+'.jpg')):
            to_render.append((pid, d, hsh))
        if d.get('renderKey') != cells_key(d) or (to_render and to_render[-1][0] == pid):
            to_atelier.append((pid, d))  # rendu absent ou périmé dans l'Atelier

    products.sort(key=lambda p: (CATEGORIES.index(p['category']), p['theme'], p['name']))
    old = json.load(open(os.path.join(REPO, 'site', 'products.json')))
    old_ids = {p['id'] for p in old}; new_ids = {p['id'] for p in products}
    old_by = {p['id']: p for p in old}
    changed_fiche = [p['id'] for p in products if p['id'] in old_by and
                     {k: v for k, v in old_by[p['id']].items() if k != 'image'} != {k: v for k, v in p.items() if k != 'image'}]
    report = {'ajoutés': sorted(new_ids-old_ids), 'retirés': sorted(old_ids-new_ids),
              'fiches modifiées': sorted(changed_fiche), 'rendus refaits': [r[0] for r in to_render],
              # à reporter dans l'Atelier : image site/images/products/<id>.jpg -> asset, puis renderId/renderedAt/renderKey
              'atelier': [{'id': pid, 'doc': d['_id'], 'renderKey': cells_key(d), 'ancienRenderId': d.get('renderId')} for pid, d in to_atelier],
              'ignorés': skipped, 'total': len(products)}
    if dry:
        print(json.dumps(report, ensure_ascii=False, indent=1)); return

    if to_render:
        sys.path.insert(0, HERE)
        import render3d
        from PIL import Image
        render3d.P.clear(); render3d.P.update(palette)
        work = render3d.WORK
        for pid, d, hsh in to_render:
            png = os.path.join(work, pid+'.png')
            render3d.render(d, png, S=1200, spp=512)
            Image.open(png).convert('RGB').save(os.path.join(IMG_DIR, pid+'.jpg'), quality=88, optimize=True)
            state['hashes'][pid] = hsh
            print('rendu', pid, flush=True)

    # images des box retirées (et anciennes images plates .png)
    for f in glob.glob(os.path.join(IMG_DIR, '*')):
        base, ext = os.path.splitext(os.path.basename(f))
        if base not in new_ids or ext != '.jpg':
            os.remove(f)
    for pid in list(state['hashes']):
        if pid not in new_ids:
            del state['hashes'][pid]

    js = json.dumps(products, ensure_ascii=False, indent=2)
    open(os.path.join(REPO, 'site', 'products.json'), 'w').write(js+'\n')
    mts = os.path.join(REPO, 'netlify', 'functions', 'products-data.mts')
    src = open(mts).read()
    open(mts, 'w').write(src[:src.index('export const products')]+'export const products = '+js+';\n')

    # sitemap : pages fixes conservées, une URL par box
    sm = os.path.join(REPO, 'site', 'sitemap.xml')
    xml = open(sm).read()
    blocks = re.findall(r'  <url>.*?</url>\n', xml, flags=re.S)
    keep = [b for b in blocks if 'produit.html?id=' not in b]
    old_lastmod = {re.search(r'id=([^<]+)</loc>', b).group(1): re.search(r'<lastmod>(.*?)</lastmod>', b).group(1)
                   for b in blocks if 'produit.html?id=' in b}
    today = datetime.date.today().isoformat()
    touched = set(report['ajoutés']) | set(report['fiches modifiées']) | set(report['rendus refaits'])
    prod = [f"  <url>\n    <loc>{SITE_URL}/produit.html?id={p['id']}</loc>\n"
            f"    <lastmod>{today if p['id'] in touched else old_lastmod.get(p['id'], today)}</lastmod>\n"
            f"    <priority>0.7</priority>\n  </url>\n" for p in products]
    head = xml[:xml.index('  <url>')]
    open(sm, 'w').write(head+''.join(keep+prod)+'</urlset>\n')

    json.dump(state, open(STATE, 'w'), ensure_ascii=False, indent=1, sort_keys=True)
    missing_hero = [h for h in HERO if h not in new_ids]
    if missing_hero:
        report['alerte'] = 'images de la page d\'accueil absentes du catalogue : ' + ', '.join(missing_hero)
    print(json.dumps(report, ensure_ascii=False, indent=1))

if __name__ == '__main__':
    main()
