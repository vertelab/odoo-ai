# -*- coding: utf-8 -*-
"""PWA controllers — service worker, manifest, icons och install-hubb.

Gör AI-chatten (/ai/chat) och hela Odoo-backend (/odoo) installerbar som
PWA med per-kund-branding (namn + logga från res.company/settings).

Routes:
- /ai/sw.js                  — pass-through service worker (scope /ai/)
- /ai/manifest.webmanifest   — manifest för AI-chatten (scope /ai/)
- /ai/icon-192.png m.fl.     — genererade ikoner (kundlogga → fallback)
- /ai/install[?app=ai|odoo]  — install-hubb med båda apparna
- /web/manifest.webmanifest  — överstyrd (kundbrandad) backend-manifest
"""

import logging

from html import escape

from odoo import http
from odoo.http import request, Response
from odoo.tools import image_process, file_open

_logger = logging.getLogger(__name__)

# Override av Odoos backend-manifest. Importen hålls guarded (samma mönster
# som stream.py) så ett importfel inte dödar /ai/*-rutterna.
try:
    from odoo.addons.web.controllers.webmanifest import WebManifest as _OdooWebManifest
except Exception:  # pragma: no cover
    _OdooWebManifest = None

# ---------------------------------------------------------------------------
# Service worker — minimal pass-through (ingen offline-cache).
# /ai/chat svarar med Cache-Control: no-store; SW:n får aldrig cachelagra
# HTML så deploys alltid levererar färsk frontend.
# ---------------------------------------------------------------------------
_SW_JS = r"""// AI Chat PWA — pass-through service worker (scope /ai/)
// Ingen cache: respekterar /ai/chat:s Cache-Control: no-store.
self.addEventListener('install', (event) => {
    // Aktivera direkt vid deploy — inget state att vänta på.
    self.skipWaiting();
});
self.addEventListener('activate', (event) => {
    event.waitUntil(self.clients.claim());
});
self.addEventListener('fetch', (event) => {
    event.respondWith(fetch(event.request));
});
"""

# ---------------------------------------------------------------------------
# Install-hubb HTML (väljare + app-specifika vyer).
# Placeholders byts i _install_html().
# ---------------------------------------------------------------------------
_INSTALL_HTML = r"""<!DOCTYPE html>
<html lang="sv">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no"/>
<title>Installera appar</title>
<link rel="manifest" href="<!-- MANIFEST_URL -->"/>
<link rel="apple-touch-icon" href="<!-- ICON_180 -->"/>
<meta name="apple-mobile-web-app-capable" content="yes"/>
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"/>
<style>
:root {
    --bg: #ffffff; --card: #f5f7fa; --border: #e0e0e0;
    --text: #1a1a2e; --muted: #666; --accent: #1976d2; --accent-hover: #1565c0;
    --radius: 12px; --font: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: var(--font); background: var(--bg); color: var(--text);
       min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 24px; }
.container { width: 100%; max-width: 420px; }
h1 { font-size: 22px; margin-bottom: 4px; }
.sub { color: var(--muted); font-size: 14px; margin-bottom: 20px; }
.app-card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
            padding: 16px; display: flex; align-items: center; gap: 14px; margin-bottom: 12px;
            text-decoration: none; color: inherit; transition: border-color .15s, box-shadow .15s; }
.app-card:hover { border-color: var(--accent); box-shadow: 0 2px 8px rgba(0,0,0,.08); }
.app-icon { width: 56px; height: 56px; border-radius: 12px; object-fit: cover; background: #fff; }
.app-info { flex: 1; }
.app-title { font-weight: 600; font-size: 16px; }
.app-desc { color: var(--muted); font-size: 13px; }
.arrow { color: var(--muted); font-size: 20px; }
.detail { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 24px; text-align: center; }
.detail .app-icon { width: 84px; height: 84px; margin-bottom: 12px; }
.detail h2 { font-size: 20px; margin-bottom: 4px; }
.back { display: inline-block; margin-bottom: 16px; color: var(--muted); text-decoration: none; font-size: 14px; }
.btn { display: none; width: 100%; padding: 14px; background: var(--accent); color: #fff; border: none;
       border-radius: 10px; font-size: 16px; font-weight: 600; cursor: pointer; margin-top: 16px; }
.btn:hover { background: var(--accent-hover); }
.btn.secondary { background: #fff; color: var(--accent); border: 1px solid var(--accent); }
.note { margin-top: 16px; color: var(--muted); font-size: 13px; line-height: 1.5; display: none; }
.note.show { display: block; }
.ios-step { display: flex; align-items: center; gap: 8px; margin-top: 8px; text-align: left; }
.ios-step .kbd { background: #fff; border: 1px solid var(--border); border-radius: 6px; padding: 2px 8px;
                 font-size: 12px; font-weight: 600; }
.installed { margin-top: 16px; padding: 10px; background: #e8f5e9; color: #2e7d32;
             border-radius: 8px; font-size: 14px; display: none; }
</style>
</head>
<body>
<div class="container">
<!-- HUB_BODY -->
</div>
<script>
(function () {
    var isIOS = /iPhone|iPad|iPod/i.test(navigator.userAgent);
    var isStandalone = window.matchMedia && window.matchMedia('(display-mode: standalone)').matches;
    var deferredPrompt = null;

    // Hub-läge: inget mer att göra.
    if (document.getElementById('hub')) return;

    var installBtn = document.getElementById('install-btn');
    var noPrompt = document.getElementById('no-prompt');
    var iosNote = document.getElementById('ios-note');
    var odooFallback = document.getElementById('odoo-fallback');
    var installedNote = document.getElementById('installed-note');

    if (isStandalone && installedNote) installedNote.style.display = 'block';

    window.addEventListener('beforeinstallprompt', function (e) {
        e.preventDefault();
        deferredPrompt = e;
        if (installBtn) {
            installBtn.style.display = 'block';
            if (noPrompt) noPrompt.style.display = 'none';
            if (odooFallback) odooFallback.style.display = 'none';
        }
    });

    if (installBtn) {
        installBtn.addEventListener('click', function () {
            if (!deferredPrompt) return;
            deferredPrompt.prompt();
            deferredPrompt.userChoice.then(function () { deferredPrompt = null; });
        });
    }

    if (isIOS) {
        if (installBtn) installBtn.style.display = 'none';
        if (iosNote) iosNote.classList.add('show');
        if (noPrompt) noPrompt.style.display = 'none';
    } else {
        // Android/övriga: om prompten inte dykt upp snart → instruktioner.
        setTimeout(function () {
            if (!deferredPrompt && !isStandalone) {
                if (noPrompt) noPrompt.classList.add('show');
                if (odooFallback) odooFallback.style.display = 'block';
            }
        }, 2500);
    }
})();
</script>
</body>
</html>
"""


def _install_hub_body(company, app=None):
    """Build the install page body. app in (None, 'ai', 'odoo')."""
    ai_name = escape(company.pwa_app_name('chat'))
    odoo_name = escape(company.pwa_app_name('backend'))

    if not app:
        return """
        <h1>Installera appar</h1>
        <div class="sub">Lägg AI-chatten eller Odoo på hemskärmen — precis som en vanlig app.</div>
        <a class="app-card" href="/ai/install?app=ai">
            <img class="app-icon" src="/ai/icon-192.png" alt="AI Chat"/>
            <div class="app-info">
                <div class="app-title">%s</div>
                <div class="app-desc">AI-medarbetarna i fickan</div>
            </div>
            <span class="arrow">›</span>
        </a>
        <a class="app-card" href="/ai/install?app=odoo">
            <img class="app-icon" src="/ai/icon-192.png" alt="Odoo"/>
            <div class="app-info">
                <div class="app-title">%s</div>
                <div class="app-desc">Hela Odoo som app</div>
            </div>
            <span class="arrow">›</span>
        </a>
        <div class="sub" style="margin-top:16px">iPhone: Dela → "Lägg till på hemskärmen" i vald app.</div>
        <div id="hub" style="display:none"></div>
        """ % (ai_name, odoo_name)

    if app == 'ai':
        title, desc, icon = ai_name, 'AI-medarbetarna i fickan', '/ai/icon-192.png'
        odoo_fb = ''
    else:
        title, desc, icon = odoo_name, 'Hela Odoo som app', '/ai/icon-192.png'
        odoo_fb = ('<a id="odoo-fallback" class="btn secondary" href="/odoo" style="display:none">'
                   'Öppna Odoo för att installera</a>')

    return """
    <a class="back" href="/ai/install">← Alla appar</a>
    <div class="detail">
        <img class="app-icon" src="%s" alt="%s"/>
        <h2>%s</h2>
        <div class="sub" style="margin-bottom:0">%s</div>
        <button id="install-btn" class="btn">Installera appen</button>
        <div id="installed-note" class="installed">Appen verkar redan vara installerad på den här enheten.</div>
        <div id="no-prompt" class="note">
            Installera via webbläsarens meny:
            <div class="ios-step"><span class="kbd">⋮</span> eller <span class="kbd">⋯</span> → <b>Installera app</b> / <b>Lägg till på startsidan</b></div>
        </div>
        <div id="ios-note" class="note">
            Lägg till på hemskärmen:
            <div class="ios-step"><span class="kbd">Dela</span> → <b>Lägg till på hemskärmen</b></div>
        </div>
        %s
    </div>
    """ % (icon, title, title, desc, odoo_fb)


class AIPwaController(http.Controller):

    # ------------------------------------------------------------------
    # Service worker (scope /ai/ — pass-through, ingen cache)
    # ------------------------------------------------------------------
    @http.route('/ai/sw.js', type='http', auth='public', methods=['GET'], sitemap=False)
    def service_worker(self):
        return Response(
            _SW_JS,
            headers=[('Content-Type', 'text/javascript'),
                     ('Cache-Control', 'no-store, must-revalidate')])

    # ------------------------------------------------------------------
    # AI Chat manifest (scope /ai/)
    # ------------------------------------------------------------------
    @http.route('/ai/manifest.webmanifest', type='http', auth='public', methods=['GET'], sitemap=False)
    def ai_manifest(self):
        company = request.env.company.sudo()
        manifest = {
            'name': company.pwa_app_name('chat'),
            'short_name': company.pwa_app_name('chat')[:12],
            'start_url': '/ai/chat',
            'scope': '/ai/',
            'display': 'standalone',
            'background_color': '#1a1a2e',
            'theme_color': '#1a1a2e',
            'icons': [
                {'src': '/ai/icon-192.png', 'sizes': '192x192', 'type': 'image/png'},
                {'src': '/ai/icon-512.png', 'sizes': '512x512', 'type': 'image/png',
                 'purpose': 'any maskable'},
            ],
        }
        return request.make_json_response(manifest, {
            'Content-Type': 'application/manifest+json'})

    # ------------------------------------------------------------------
    # Ikoner (192/512 för manifest, 180 = apple-touch-icon)
    # ------------------------------------------------------------------
    @http.route('/ai/icon-<int:size>.png', type='http', auth='public', methods=['GET'], sitemap=False)
    def ai_icon(self, size=192):
        if size not in (192, 512, 180):
            size = 192
        company = request.env.company.sudo()
        source = company.pwa_icon_bytes()
        if not source:
            return Response(status=404)
        try:
            img = image_process(
                source, size=(size, size), expand=True,
                colorize=(255, 255, 255), padding=16)
        except Exception:
            _logger.exception("PWA icon generation failed (size=%s)", size)
            return Response(status=500)
        return Response(img, headers=[
            ('Content-Type', 'image/png'),
            ('Cache-Control', 'public, max-age=3600')])

    # ------------------------------------------------------------------
    # Install-hubb: /ai/install (väljare) + ?app=ai|odoo (app-vyer)
    # ------------------------------------------------------------------
    @http.route('/ai/install', type='http', auth='public', methods=['GET'], sitemap=False)
    def install_hub(self, app=None, **kw):
        company = request.env.company.sudo()
        app = app if app in ('ai', 'odoo') else None
        manifest_url = '/ai/manifest.webmanifest' if app != 'odoo' else '/web/manifest.webmanifest'
        body = _install_hub_body(company, app)
        html = (_INSTALL_HTML
                .replace('<!-- MANIFEST_URL -->', manifest_url)
                .replace('<!-- ICON_180 -->', '/ai/icon-180.png')
                .replace('<!-- HUB_BODY -->', body))
        return Response(html, headers=[
            ('Content-Type', 'text/html; charset=utf-8'),
            ('Cache-Control', 'no-store, must-revalidate')])


# ---------------------------------------------------------------------------
# Kundbrandat backend-manifest (/web/manifest.webmanifest)
# ---------------------------------------------------------------------------
if _OdooWebManifest is not None:
    class WebManifest(_OdooWebManifest):

        @http.route('/web/manifest.webmanifest', type='http', auth='public',
                    methods=['GET'], readonly=True)
        def webmanifest(self):
            return super().webmanifest()

        def _get_webmanifest(self):
            manifest = super()._get_webmanifest()
            company = request.env.company.sudo()
            manifest['name'] = company.pwa_app_name('backend')
            manifest['icons'] = [
                {'src': '/ai/icon-192.png', 'sizes': '192x192', 'type': 'image/png'},
                {'src': '/ai/icon-512.png', 'sizes': '512x512', 'type': 'image/png',
                 'purpose': 'any maskable'},
            ]
            return manifest
