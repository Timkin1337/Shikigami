#!/bin/sh
# Builds assets/hero3d.js from src/hero3d.js with the app's own three.js + three-vrm (dev/shell/node_modules).
cd "$(dirname "$0")"
NODE_PATH=../dev/shell/node_modules ../dev/promo/node_modules/.bin/esbuild src/hero3d.js \
  --bundle --minify --format=esm --target=es2020 --outfile=assets/hero3d.js
# new version stamp on asset links, so browsers don't keep old site.js/site.css (GitHub Pages caches for 10 min)
V=$(date +%Y%m%d%H%M)
sed -i -E "s#assets/site\.(css|js)(\?v=[0-9]+)?\"#assets/site.\1?v=$V\"#g; s#'\./assets/hero3d\.js(\?v=[0-9]+)?'#'./assets/hero3d.js?v=$V'#" *.html
