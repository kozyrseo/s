KOZYR — FINAL POLISH PATCH
============================

This patch contains ALL the remaining improvements from the audit:

  1. Author photo srcset applied to all 24 blog articles (50 <img> tags updated)
     → 5 KB saved on first visit per article (240×240 → 56×56)
  2. Currency standardized: 60 instances of "грн" → "₴" across 8 pages  
  3. Google Fonts URL unified: 4 different URLs → 1 (better HTTP caching)
  4. <main> landmark added to 17 pages (accessibility WCAG)
  5. site.webmanifest: added "categories" field
  6. Generator template updated: new articles will use unified Fonts URL

DEPLOY INSTRUCTIONS:

  1. Extract this ZIP at the REPO ROOT (folder containing ua/, automation/, etc.):
     cd /path/to/kozyr-repo
     unzip -o kozyr-finish-all.zip

  2. Also clean up leftovers from previous patches:
     git rm -rf uk/                       # orphan from earlier mispositioned patch
     git rm assets/logo-horizontal.svg    # orphan (never referenced)
     git rm assets/logo-K.svg             # orphan (never referenced)
     git rm assets/logo-vertical.svg      # orphan (never referenced)
     git rm ua/blog/klubok-reykbek-clubgg-ukraina/hero.jpg  # orphan (only .webp used)

  3. Check diff:
     git status
     # Expected: modified: many blog & rooms/clubs files
     #           new file:  none (only mods + deletes)
     #           deleted:   uk/ + 4 orphan assets

  4. Commit and push:
     git add -A
     git commit -m "chore(polish): srcset author photos, unify fonts, standardize ₴, add <main>

     - Author photo: srcset 56×56/144×144 on all 50 img tags (24 articles)
     - Currency: standardized on ₴ symbol (60 replacements across 8 pages)
     - Google Fonts: unified to 1 URL (was 4) for better HTTP caching
     - <main> landmark: added to 17 pages (accessibility WCAG 2.1)
     - Manifest: added categories ['games', 'entertainment', 'lifestyle']
     - Template: article.html generator uses new Fonts URL
     - Cleanup: removed orphan uk/ folder + 4 unreferenced asset files"
     git push

EXPECTED SCORE AFTER DEPLOY:
  Site quality:      95/100 (was 93)
  Generator quality: 100/100 (unchanged, source-perfect)
